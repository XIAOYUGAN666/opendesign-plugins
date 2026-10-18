"""CLI adapters for the single common artifact finalizer/verifier."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from scripts import build_plugin, build_release, verify_release


MAX_ARCHIVE_BYTES = 1024 * 1024 * 1024
MAX_RECEIPT_BYTES = 1024 * 1024


def _bounded_read(path: Path, maximum: int) -> bytes:
    with path.open("rb") as stream:
        content = stream.read(maximum + 1)
    if not content or len(content) > maximum:
        raise ValueError("invalidArtifactSize")
    return content


def receipt_path(archive: Path) -> Path:
    return archive.with_suffix(".receipt.json")


def copy_verified_pair(archive: Path, receipt: Path, output: Path) -> None:
    """Map the already verified pair without overwriting any caller data."""
    target_receipt = receipt_path(output)
    if output.exists() or target_receipt.exists():
        raise ValueError("outputMustBeFresh")
    receipt_bytes = _bounded_read(receipt, MAX_RECEIPT_BYTES)
    metadata = json.loads(receipt_bytes)
    payload = _bounded_read(archive, MAX_ARCHIVE_BYTES)
    if metadata["archive"]["byteLength"] != len(payload) or metadata["archive"][
        "sha256"
    ] != build_release.sha256_digest(payload):
        raise ValueError("archiveReceiptMismatch")
    output.parent.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    try:
        for target, content in ((output, payload), (target_receipt, receipt_bytes)):
            with target.open("xb") as stream:
                created.append(target)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
    except Exception:
        for target in reversed(created):
            target.unlink(missing_ok=True)
        raise


def _common_builder(plugin_root: Path):
    public_scripts = plugin_root.parents[1] / "scripts"
    sys.path.insert(0, str(public_scripts))
    try:
        spec = importlib.util.spec_from_file_location(
            "_public_artifact_builder", public_scripts / "build_artifact.py"
        )
        if spec is None or spec.loader is None:
            raise ValueError("missingCommonBuilder")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(public_scripts))


def build(
    plugin_root: Path,
    output: Path,
    *,
    release_sequence: int,
    runtime_archive: Path | None,
    dotnet_exe: str | None,
    uv_exe: str | None,
) -> None:
    if output.exists() or receipt_path(output).exists():
        raise ValueError("outputMustBeFresh")
    common = _common_builder(plugin_root)
    common.check_source_closure(plugin_root)
    common.check_release_inputs(plugin_root, release_sequence=release_sequence)
    environment = {
        key: os.environ[key]
        for key in (
            "SystemRoot",
            "WINDIR",
            "TEMP",
            "TMP",
            "PATH",
            "USERPROFILE",
            "LOCALAPPDATA",
            "APPDATA",
        )
        if key in os.environ
    }
    with tempfile.TemporaryDirectory(
        prefix="rhino-common-output-", dir=build_release._safe_ascii_temp_root()
    ) as temporary:
        workspace = Path(temporary)
        staging_output = workspace / "candidate"
        build_plugin.stage(
            plugin_root,
            staging_output,
            workspace / "downloads",
            runtime_archive=runtime_archive,
            dotnet_exe=dotnet_exe,
            uv_exe=uv_exe,
        )
        archive = common._finish_staged_artifact(
            plugin_root,
            staging_output,
            Path(sys.executable),
            environment,
            False,
            release_sequence=release_sequence,
        )
        verify(
            plugin_root,
            archive,
            staging_output / "unsigned-receipt.json",
            unsigned=True,
            trusted_keys=None,
            dotnet_exe=dotnet_exe,
            output=output,
        )


def verify(
    plugin_root: Path,
    archive: Path,
    receipt: Path,
    *,
    unsigned: bool,
    trusted_keys: Path | None,
    dotnet_exe: str | None,
    output: Path | None = None,
) -> None:
    if not unsigned and trusted_keys is None:
        raise ValueError("trustedKeysRequired")
    if unsigned and trusted_keys is not None:
        raise ValueError("ambiguousVerificationAuthority")
    archive_bytes = _bounded_read(archive, MAX_ARCHIVE_BYTES)
    receipt_bytes = _bounded_read(receipt, MAX_RECEIPT_BYTES)
    with tempfile.TemporaryDirectory(prefix="rhino-verified-snapshot-") as folder:
        snapshot_archive = Path(folder) / "candidate.zip"
        snapshot_receipt = Path(folder) / "receipt.json"
        for path, content in (
            (snapshot_archive, archive_bytes),
            (snapshot_receipt, receipt_bytes),
        ):
            with path.open("xb") as stream:
                stream.write(content)
        _verify_snapshot(
            plugin_root,
            snapshot_archive,
            snapshot_receipt,
            unsigned=unsigned,
            trusted_keys=trusted_keys,
            dotnet_exe=dotnet_exe,
        )
        if output is not None:
            copy_verified_pair(snapshot_archive, snapshot_receipt, output)


def _verify_snapshot(
    plugin_root: Path,
    archive: Path,
    receipt: Path,
    *,
    unsigned: bool,
    trusted_keys: Path | None,
    dotnet_exe: str | None,
) -> None:
    verify_release.PLUGIN_ROOT = plugin_root
    public_root = plugin_root.parents[1]
    command = [
        sys.executable,
        "-B",
        str(public_root / "scripts/verify_artifact.py"),
        str(archive),
        str(receipt),
        str(public_root / "schemas"),
    ]
    if unsigned:
        if trusted_keys is not None:
            raise ValueError("ambiguousVerificationAuthority")
        command.append("--unsigned")
    elif trusted_keys is not None:
        keys = json.loads(_bounded_read(trusted_keys, MAX_RECEIPT_BYTES))
        key_id = json.loads(receipt.read_bytes())["publisherKeyId"]
        public_key = keys.get(key_id) if isinstance(keys, dict) else None
        if not isinstance(public_key, str) or len(bytes.fromhex(public_key)) != 32:
            raise ValueError("trustedKeysRequired")
        command.extend(("--key-id", key_id, "--public-key", public_key))
    else:
        raise ValueError("trustedKeysRequired")
    subprocess.run(
        command, cwd=public_root, check=True, capture_output=True, timeout=300
    )
    # Trusted source verifier inspects actual extracted assemblies after common
    # archive/receipt/root/source verification; archive code is never imported.
    with zipfile.ZipFile(archive) as outer:
        identity = json.loads(outer.read("plugin.json"))
        native = identity["extensions"]["io.github.xiaoyugan666.native"]["native"]
        release = json.loads(outer.read("release.json"))
        payload = outer.read(native["relativePath"])
    verify_release.verify_yak_archive(
        payload,
        expected_plugin_version=identity["version"],
        expected_plugin_guid=native["pluginId"],
    )
    selected_dotnet = dotnet_exe or build_release._dotnet_executable()
    with tempfile.TemporaryDirectory(prefix="rhino-public-native-") as temporary:
        yak_path = Path(temporary) / "native.yak"
        yak_path.write_bytes(payload)
        yak_root = Path(temporary) / "yak"
        with zipfile.ZipFile(yak_path) as yak:
            verify_release._extract_members(yak, yak_root, set(yak.namelist()))
        verify_release._run_native_identity_test(
            yak_root,
            selected_dotnet,
            native["capabilityDigest"],
            release["providerBuildId"],
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("build", "verify"))
    parser.add_argument("--plugin-root", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--python-runtime-archive", type=Path)
    parser.add_argument("--release-sequence", type=int)
    parser.add_argument("--dotnet-exe")
    parser.add_argument("--uv-exe")
    parser.add_argument("--unsigned", action="store_true")
    parser.add_argument("--trusted-keys", type=Path)
    args = parser.parse_args()
    root = args.plugin_root.resolve(strict=True)
    build_release.configure_plugin_root(root)
    verify_release.PLUGIN_ROOT = root
    if args.action == "build":
        if args.release_sequence is None:
            parser.error("--release-sequence is required for build")
        output = (
            args.output or root / "provider/dist" / build_release.ARCHIVE_NAME
        ).resolve()
        build(
            root,
            output,
            release_sequence=args.release_sequence,
            runtime_archive=args.python_runtime_archive,
            dotnet_exe=args.dotnet_exe,
            uv_exe=args.uv_exe,
        )
        print("Built common unsigned candidate and paired receipt.")
    else:
        if args.archive is None:
            parser.error("--archive is required")
        archive = args.archive.resolve(strict=True)
        verify(
            root,
            archive,
            (args.receipt or receipt_path(archive)).resolve(strict=True),
            unsigned=args.unsigned,
            trusted_keys=args.trusted_keys,
            dotnet_exe=args.dotnet_exe,
        )
        print("Verified common artifact, receipt and native assemblies.")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        raise SystemExit("artifactReleaseFailed") from None
