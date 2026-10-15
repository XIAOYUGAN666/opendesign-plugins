"""Independently verify a self-contained Provider + Yak release archive."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.release_probe import ReleaseProbeError, probe_managed_runtime


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = PROJECT_ROOT.parent
PLUGIN_GUID = "74a1e8f4-1134-4686-a3e5-ee8bf393dec9"
YAK_MEMBER = "native/rhino-mcp-bridge-0.1.2-rh8-win.yak"
RUNTIME_ROOT_FILES = frozenset(
    {
        "python.exe",
        "python312.dll",
        "python3.dll",
        "vcruntime140.dll",
        "vcruntime140_1.dll",
        "pyexpat.pyd",
        "select.pyd",
        "unicodedata.pyd",
        "winsound.pyd",
        "_asyncio.pyd",
        "_bz2.pyd",
        "_ctypes.pyd",
        "_decimal.pyd",
        "_elementtree.pyd",
        "_hashlib.pyd",
        "_lzma.pyd",
        "_msi.pyd",
        "_multiprocessing.pyd",
        "_overlapped.pyd",
        "_queue.pyd",
        "_socket.pyd",
        "_sqlite3.pyd",
        "_ssl.pyd",
        "_uuid.pyd",
        "_wmi.pyd",
        "_zoneinfo.pyd",
        "libcrypto-3.dll",
        "libffi-8.dll",
        "libssl-3.dll",
        "sqlite3.dll",
        "python312.zip",
        "python312._pth",
        "LICENSE.txt",
        "python.cat",
    }
)
_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_FORBIDDEN_PRODUCT_DIRECTORY = "open" + "design"
_LEGACY_MANUAL_LAUNCHERS = tuple("mcp" + suffix for suffix in ("start", "stop"))


class ReleaseVerificationError(ValueError):
    """A release member, digest, contract, or runtime check failed."""


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _digest(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _safe_member(member: str) -> PurePosixPath:
    if not member or "\\" in member or "\x00" in member:
        raise ReleaseVerificationError("Release contains an invalid member path.")
    path = PurePosixPath(member)
    if path.is_absolute() or path.as_posix() != member:
        raise ReleaseVerificationError(
            "Release member is not a normalized relative path."
        )
    if any(part in ("", ".", "..") for part in path.parts):
        raise ReleaseVerificationError("Release member escapes its archive root.")
    return path


def validate_release_hygiene(members: set[str]) -> None:
    """Reject unsafe paths, development content, and unbundled interpreters."""
    if not members:
        raise ReleaseVerificationError("Release archive is empty.")
    for member in members:
        path = _safe_member(member)
        folded_parts = tuple(part.casefold() for part in path.parts)
        if (
            folded_parts[0] in {"provider", "src", "tests"}
            or "tests" in folded_parts
            or any(
                part in {_FORBIDDEN_PRODUCT_DIRECTORY, ".git", "__pycache__"}
                for part in folded_parts
            )
        ):
            raise ReleaseVerificationError(
                f"Release contains a forbidden source/development directory: {member}"
            )
        if path.parts[0] not in {
            "manifest.json",
            "file-manifest.json",
            "SBOM.spdx.json",
            "runtime",
            "rhino_mcp",
            "contracts",
            "native",
        }:
            raise ReleaseVerificationError(
                "Release member is outside the approved package layout."
            )
        suffix = path.suffix.casefold()
        if suffix in {
            ".pdb",
            ".pyc",
            ".pyo",
            ".ps1",
            ".psm1",
            ".cmd",
            ".bat",
            ".com",
            ".sh",
        }:
            raise ReleaseVerificationError(
                "Release contains a development or install-script file."
            )
        if suffix == ".exe" and member != "runtime/python/python.exe":
            raise ReleaseVerificationError(
                f"Release contains an unexpected executable: {member}"
            )
        if "python311" in member.casefold():
            raise ReleaseVerificationError(
                "Release contains an unexpected Python runtime version."
            )
        if any(token in path.name.casefold() for token in _LEGACY_MANUAL_LAUNCHERS):
            raise ReleaseVerificationError("Release contains a public manual launcher.")
        if path.parts[0] == "runtime":
            if len(path.parts) < 3 or path.parts[1] != "python":
                raise ReleaseVerificationError(
                    "Private Python runtime has an unexpected layout."
                )
            if len(path.parts) == 3 and path.parts[2] not in RUNTIME_ROOT_FILES:
                raise ReleaseVerificationError(
                    "Private Python runtime has an unexpected root member."
                )
            if suffix == ".pyd" and len(path.parts) > 3 and path.parts[2] != "Lib":
                raise ReleaseVerificationError(
                    "Native Python extensions are outside the runtime site tree."
                )
        if path.parts[0] == "native" and (
            len(path.parts) != 2 or not member.endswith(".yak")
        ):
            raise ReleaseVerificationError(
                "Outer native content must be exactly one Yak archive."
            )
        lowered = member.casefold()
        if any(
            marker in lowered
            for marker in (
                "origin.md",
                "upstream.md",
                "direct_url.json",
                *_LEGACY_MANUAL_LAUNCHERS,
            )
        ):
            raise ReleaseVerificationError(
                "Release contains upstream or manual-launch metadata."
            )


def verify_yak_archive(
    payload: bytes,
    *,
    expected_plugin_version: str,
    expected_plugin_guid: str,
) -> set[str]:
    """Check root Yak manifest and the two Rhino 8 target directories."""
    from io import BytesIO

    try:
        with zipfile.ZipFile(BytesIO(payload)) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(names) != len(set(name.casefold() for name in names)):
                raise ReleaseVerificationError("Yak contains duplicate member names.")
            for info in infos:
                _safe_member(info.filename)
                mode = info.external_attr >> 16
                if mode and (mode & 0o170000) not in (0, 0o100000, 0o040000):
                    raise ReleaseVerificationError(
                        "Yak contains a link or special file."
                    )
            if archive.testzip() is not None:
                raise ReleaseVerificationError("Yak CRC validation failed.")
            files = set(names)
            manifest = archive.read("manifest.yml").decode("utf-8")
    except (OSError, UnicodeDecodeError, KeyError, zipfile.BadZipFile) as exc:
        raise ReleaseVerificationError(
            "Yak archive is unreadable or lacks its root manifest."
        ) from exc

    required_manifest = {
        "name: rhino-mcp-bridge",
        f"version: {expected_plugin_version}",
        f"  - guid:{expected_plugin_guid}",
    }
    if not all(line in manifest.splitlines() for line in required_manifest):
        raise ReleaseVerificationError(
            "Yak root manifest has the wrong package or plug-in identity."
        )
    for target in ("net48", "net8.0"):
        if f"{target}/RhinoMcpBridge.rhp" not in files:
            raise ReleaseVerificationError(
                f"Yak does not contain the {target} plug-in assembly."
            )
        if not any(
            name.startswith(f"{target}/") and name.endswith(".dll") for name in files
        ):
            raise ReleaseVerificationError(
                f"Yak does not contain {target} dependency assemblies."
            )
    if any(
        PurePosixPath(name).name.casefold()
        in {"rhinocommon.dll", "grasshopper.dll", "rhino.ui.dll"}
        or (
            PurePosixPath(name).name.casefold().startswith("eto.")
            and name.casefold().endswith(".dll")
        )
        for name in files
    ):
        raise ReleaseVerificationError("Yak contains software-owned assemblies.")
    if any(name.startswith(("provider/", "src/", "tests/")) for name in files):
        raise ReleaseVerificationError(
            "Yak contains a forbidden source/development layout."
        )
    if any(name.casefold().endswith((".pdb", ".exe")) for name in files):
        raise ReleaseVerificationError("Yak contains debug symbols or an executable.")
    return files


def _load_json(payload: bytes, description: str) -> Any:
    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseVerificationError(
            f"{description} is not valid UTF-8 JSON."
        ) from exc


def _require_digest(value: Any, description: str) -> str:
    if not isinstance(value, str) or not _DIGEST_PATTERN.fullmatch(value):
        raise ReleaseVerificationError(f"{description} digest is invalid.")
    return value


def _check_digest(value: Any, actual: str, description: str) -> None:
    if _require_digest(value, description) != actual:
        raise ReleaseVerificationError(f"{description} digest does not match.")


def _extract_members(
    archive: zipfile.ZipFile, destination: Path, members: set[str]
) -> None:
    for member in sorted(members):
        path = _safe_member(member)
        target = destination.joinpath(*path.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(archive.read(member))


def _run_native_identity_test(
    yak_root: Path,
    dotnet_exe: str,
    expected_capability_digest: str,
    expected_provider_build_id: str | None = None,
) -> None:
    artifact_root = Path(tempfile.mkdtemp(prefix="rhino-identity-artifacts-"))
    environment = os.environ.copy()
    environment["RHINO_NATIVE_OUTPUT_ROOT"] = str(yak_root.resolve())
    environment["RHINO_EXPECTED_CAPABILITY_DIGEST"] = _require_digest(
        expected_capability_digest,
        "Native capability",
    )
    if expected_provider_build_id is not None:
        environment["RHINO_EXPECTED_PROVIDER_BUILD_ID"] = _require_digest(
            expected_provider_build_id, "Provider build"
        )
    result = subprocess.run(
        [
            dotnet_exe,
            "test",
            str(PLUGIN_ROOT / "native" / "tests" / "RhinoMcpBridge.Tests.csproj"),
            "--configuration",
            "Release",
            "--filter",
            "FullyQualifiedName~BothRhino8TargetsHaveTheSamePermanentPluginIdentity",
            "--artifacts-path",
            str(artifact_root),
            "-p:RestoreLockedMode=true",
            "-p:ContinuousIntegrationBuild=true",
        ],
        cwd=PLUGIN_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        raise ReleaseVerificationError("Packaged native assembly identity test failed.")


def verify_release_archive(
    archive_path: Path,
    *,
    dotnet_exe: str | None = None,
    runtime_probe: bool = True,
) -> dict[str, Any]:
    """Independently check archive digests, Yak identity, .NET TFMs, and MCP discovery."""
    archive_path = archive_path.resolve(strict=True)
    try:
        with zipfile.ZipFile(archive_path) as archive:
            infos = archive.infolist()
            members = [info.filename for info in infos]
            if len(members) != len(set(member.casefold() for member in members)):
                raise ReleaseVerificationError(
                    "Release archive contains duplicate members."
                )
            if any(info.is_dir() for info in infos):
                raise ReleaseVerificationError(
                    "Release archive must contain files only, not explicit directories."
                )
            member_set = set(members)
            validate_release_hygiene(member_set)
            required = {
                "manifest.json",
                "file-manifest.json",
                "SBOM.spdx.json",
                "runtime/python/python.exe",
                "runtime/python/python312._pth",
                "rhino_mcp/managed_bootstrap.py",
                "contracts/protocol.json",
                "contracts/provider-build.json",
                "contracts/tool-catalog.json",
                YAK_MEMBER,
            }
            if not required <= member_set:
                raise ReleaseVerificationError(
                    "Release is missing required composite-profile files."
                )
            manifest_bytes = archive.read("manifest.json")
            inventory_bytes = archive.read("file-manifest.json")
            manifest = _load_json(manifest_bytes, "Release manifest")
            inventory = _load_json(inventory_bytes, "Release file manifest")
            if not isinstance(manifest, dict) or not isinstance(inventory, dict):
                raise ReleaseVerificationError(
                    "Release manifests must be JSON objects."
                )
            if set(manifest) != {
                "manifestSchemaVersion",
                "packageId",
                "pluginVersion",
                "releaseSequence",
                "artifactVariantId",
                "platform",
                "hostContract",
                "agentContract",
                "startupEnvelopeVersion",
                "entrypoint",
                "module",
                "components",
                "mcp",
                "software",
                "adapter",
                "providerBuildId",
                "toolCatalogDigest",
                "configurationSchemaVersion",
                "filesDigest",
                "sbomDigest",
                "native",
            }:
                raise ReleaseVerificationError(
                    "Release manifest has unknown or missing fields."
                )
            if (
                manifest.get("manifestSchemaVersion") != 1
                or manifest.get("packageId") != "rhino"
                or manifest.get("pluginVersion") != "0.1.2"
                or manifest.get("entrypoint") != "runtime/python/python.exe"
                or manifest.get("module") != "rhino_mcp.managed_bootstrap"
                or manifest.get("mcp")
                != {"transport": "streamable-http", "path": "/mcp"}
                or manifest.get("artifactVariantId") != "windows-x86_64-rhino-8"
            ):
                raise ReleaseVerificationError(
                    "Release manifest does not match the approved Rhino identity."
                )
            if manifest.get("components") != [
                {"kind": "provider", "id": "rhino"},
                {"kind": "native", "id": "rhino-mcp-bridge"},
            ]:
                raise ReleaseVerificationError(
                    "Release manifest component profile is invalid."
                )
            native = manifest.get("native")
            if not isinstance(native, dict) or native != {
                "packageId": "rhino-mcp-bridge",
                "version": "0.1.2",
                "relativePath": YAK_MEMBER,
                "sha256": native.get("sha256"),
                "contractVersion": 1,
                "contractDigest": native.get("contractDigest"),
                "capabilityDigest": native.get("capabilityDigest"),
                "pluginId": PLUGIN_GUID,
            }:
                raise ReleaseVerificationError(
                    "Release native package identity is invalid."
                )
            _require_digest(native.get("capabilityDigest"), "Native capability")

            _check_digest(
                manifest.get("filesDigest"),
                _digest(_canonical_json_bytes(inventory)),
                "File manifest",
            )
            if inventory.get("schemaVersion") != 1 or not isinstance(
                inventory.get("files"), list
            ):
                raise ReleaseVerificationError(
                    "Release file manifest schema is invalid."
                )
            entries = inventory["files"]
            paths: list[str] = []
            for entry in entries:
                if not isinstance(entry, dict) or set(entry) != {
                    "path",
                    "byteLength",
                    "sha256",
                }:
                    raise ReleaseVerificationError(
                        "Release file manifest entry has an invalid shape."
                    )
                path = entry["path"]
                if not isinstance(path, str):
                    raise ReleaseVerificationError(
                        "Release file manifest path is invalid."
                    )
                _safe_member(path)
                if (
                    not isinstance(entry["byteLength"], int)
                    or isinstance(entry["byteLength"], bool)
                    or entry["byteLength"] < 0
                ):
                    raise ReleaseVerificationError(
                        "Release file manifest length is invalid."
                    )
                if not isinstance(
                    entry["sha256"], str
                ) or not _DIGEST_PATTERN.fullmatch(entry["sha256"]):
                    raise ReleaseVerificationError(
                        "Release file manifest digest is invalid."
                    )
                paths.append(path)
            if paths != sorted(paths) or len(paths) != len(
                set(path.casefold() for path in paths)
            ):
                raise ReleaseVerificationError(
                    "Release file manifest must be sorted and case-insensitively unique."
                )
            expected_members = set(paths) | {"manifest.json", "file-manifest.json"}
            if expected_members != member_set:
                raise ReleaseVerificationError(
                    "Release archive and file manifest member sets differ."
                )
            for entry in entries:
                payload = archive.read(entry["path"])
                if (
                    len(payload) != entry["byteLength"]
                    or _digest(payload) != entry["sha256"]
                ):
                    raise ReleaseVerificationError(
                        f"Release file digest mismatch: {entry['path']}."
                    )

            sbom_bytes = archive.read("SBOM.spdx.json")
            _check_digest(manifest.get("sbomDigest"), _digest(sbom_bytes), "SBOM")
            sbom = _load_json(sbom_bytes, "SPDX SBOM")
            if (
                not isinstance(sbom, dict)
                or sbom.get("spdxVersion") != "SPDX-2.3"
                or not isinstance(sbom.get("packages"), list)
            ):
                raise ReleaseVerificationError(
                    "Release SBOM is not an SPDX 2.3 document."
                )
            provider_build = archive.read("contracts/provider-build.json")
            _check_digest(
                manifest.get("providerBuildId"),
                _digest(provider_build),
                "Provider build",
            )
            catalog_bytes = archive.read("contracts/tool-catalog.json")
            catalog = _load_json(catalog_bytes, "MCP tool catalog")
            if (
                not isinstance(catalog, dict)
                or catalog.get("normalizationVersion") != 1
                or not isinstance(catalog.get("tools"), list)
            ):
                raise ReleaseVerificationError(
                    "MCP catalog normalization contract is invalid."
                )
            _check_digest(
                manifest.get("toolCatalogDigest"),
                _digest(_canonical_json_bytes(catalog)),
                "MCP tool catalog",
            )
            protocol = archive.read("contracts/protocol.json")
            _check_digest(
                native.get("contractDigest"),
                _digest(protocol),
                "Native protocol contract",
            )
            yak_bytes = archive.read(YAK_MEMBER)
            _check_digest(native.get("sha256"), _digest(yak_bytes), "Yak package")
            yak_members = verify_yak_archive(
                yak_bytes,
                expected_plugin_version="0.1.2",
                expected_plugin_guid=PLUGIN_GUID,
            )
            if (
                "net48/RhinoMcpBridge.rhp" not in yak_members
                or "net8.0/RhinoMcpBridge.rhp" not in yak_members
            ):
                raise ReleaseVerificationError(
                    "Yak does not package both approved Rhino target frameworks."
                )

            extracted = Path(tempfile.mkdtemp(prefix="rhino-release-verify-"))
            _extract_members(archive, extracted, member_set)
    except (OSError, zipfile.BadZipFile) as exc:
        raise ReleaseVerificationError(
            "Release archive could not be read safely."
        ) from exc

    if (extracted / "runtime" / "python" / "python312._pth").read_text(
        encoding="ascii"
    ) != ("python312.zip\n.\nLib\\site-packages\n..\\..\nimport site\n"):
        raise ReleaseVerificationError(
            "Private Python path configuration is not isolated to the release tree."
        )

    yak_root = Path(tempfile.mkdtemp(prefix="rhino-yak-verify-"))
    try:
        with zipfile.ZipFile(extracted / YAK_MEMBER) as yak:
            _extract_members(yak, yak_root, set(yak.namelist()))
        selected_dotnet = (
            dotnet_exe or os.environ.get("RHINO_DOTNET_EXE") or shutil.which("dotnet")
        )
        if not selected_dotnet or not Path(selected_dotnet).is_file():
            raise ReleaseVerificationError(
                "Set RHINO_DOTNET_EXE or add the .NET 8 SDK to PATH for native identity verification."
            )
        _run_native_identity_test(
            yak_root,
            str(Path(selected_dotnet).resolve()),
            native["capabilityDigest"],
            manifest["providerBuildId"],
        )
    except (OSError, zipfile.BadZipFile) as exc:
        raise ReleaseVerificationError(
            "The packaged Yak could not be extracted for identity verification."
        ) from exc

    counts: dict[str, int] | None = None
    if runtime_probe:
        try:
            actual_catalog, counts = probe_managed_runtime(
                extracted, manifest["providerBuildId"]
            )
        except ReleaseProbeError as exc:
            raise ReleaseVerificationError(
                "The packaged private Provider runtime failed MCP discovery."
            ) from exc
        if counts != {"tools": 70, "resourcesAndTemplates": 4, "prompts": 2}:
            raise ReleaseVerificationError(
                f"Packaged MCP discovery counts changed: {counts}."
            )
        if actual_catalog != catalog:
            raise ReleaseVerificationError(
                "Packaged MCP discovery does not match its signed catalog preview."
            )

    return {
        "archive": str(archive_path),
        "sha256": _digest(archive_path.read_bytes()),
        "members": len(member_set),
        "nativeTargets": ["net48", "net8.0"],
        "mcp": counts,
    }


def main(argv: list[str] | None = None) -> int:
    global PLUGIN_ROOT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path, nargs="?")
    parser.add_argument("--yak-path", type=Path)
    parser.add_argument("--provider-build-id")
    parser.add_argument("--capability-digest")
    parser.add_argument("--plugin-root", type=Path, default=PLUGIN_ROOT)
    parser.add_argument("--dotnet-exe")
    parser.add_argument("--skip-runtime-probe", action="store_true")
    arguments = parser.parse_args(argv)
    PLUGIN_ROOT = arguments.plugin_root.resolve()
    try:
        if arguments.yak_path is not None:
            if not arguments.provider_build_id or not arguments.capability_digest:
                raise ReleaseVerificationError(
                    "Native identity expectations are required."
                )
            dotnet = arguments.dotnet_exe or shutil.which("dotnet")
            if not dotnet:
                raise ReleaseVerificationError("The .NET SDK is required.")
            payload = arguments.yak_path.read_bytes()
            verify_yak_archive(
                payload,
                expected_plugin_version="0.1.2",
                expected_plugin_guid=PLUGIN_GUID,
            )
            with tempfile.TemporaryDirectory(prefix="rhino-yak-verify-") as folder:
                yak_root = Path(folder)
                with zipfile.ZipFile(arguments.yak_path) as yak:
                    _extract_members(yak, yak_root, set(yak.namelist()))
                _run_native_identity_test(
                    yak_root,
                    dotnet,
                    arguments.capability_digest,
                    arguments.provider_build_id,
                )
            print("Verified native package identity.")
            return 0
        if arguments.archive is None:
            raise ReleaseVerificationError("A release archive is required.")
        result = verify_release_archive(
            arguments.archive,
            dotnet_exe=arguments.dotnet_exe,
            runtime_probe=not arguments.skip_runtime_probe,
        )
    except ReleaseVerificationError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(
        f"Verified {result['members']} files; targets={','.join(result['nativeTargets'])}; "
        f"sha256={result['sha256']}"
    )
    if result["mcp"] is not None:
        counts = result["mcp"]
        print(
            "MCP discovery: "
            f"{counts['tools']} tools, {counts['resourcesAndTemplates']} resources/templates, "
            f"{counts['prompts']} prompts"
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        raise SystemExit("artifactVerificationFailed") from None
