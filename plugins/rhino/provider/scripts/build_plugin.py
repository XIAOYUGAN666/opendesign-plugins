"""Thin Rhino staging adapter for the common unsigned artifact builder."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import tomllib
import zipfile
from pathlib import Path
from typing import Literal

from mcp.types import Tool
from packaging.tags import sys_tags
from packaging.utils import canonicalize_name, parse_wheel_filename
from pydantic import BaseModel, ConfigDict, Field

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import build_release as release
from scripts.release_probe import probe_managed_runtime


class ExactModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ToolPreviewV1(ExactModel):
    normalizationVersion: Literal[1]
    tools: list[Tool]


class SourceFile(ExactModel):
    path: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class NativeBinding(ExactModel):
    path: str
    targetFrameworks: list[Literal["net48", "net8.0"]]
    pluginId: str
    capabilityDigest: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")


class CanonicalBuildIdentity(ExactModel):
    profile: Literal["native-build-record-v1"]
    recordPath: Literal["contracts/provider-build.json"]


class RiskReviewV1(ExactModel):
    schemaVersion: Literal[1]
    status: Literal["pending-review/provider-candidate"]
    engineeringAcceptance: Literal["not-performed"]
    previewDigest: str
    providerBuildId: str
    sourceDigest: str
    sourceFiles: list[SourceFile]
    buildIdentity: CanonicalBuildIdentity


def runtime_inventory(staging: Path, plugin_root: Path, native: dict) -> dict:
    """Bind installed wheel versions to the existing frozen uv archive hashes."""
    import email.parser

    tags = {tag: index for index, tag in enumerate(sys_tags())}
    locked = tomllib.loads((plugin_root / "provider/uv.lock").read_text("utf-8"))
    wheels = []
    for metadata_path in sorted(
        (staging / "runtime/python/Lib/site-packages").glob("*.dist-info/METADATA")
    ):
        metadata = email.parser.Parser().parsestr(metadata_path.read_text("utf-8"))
        name, version = canonicalize_name(metadata["Name"]), metadata["Version"]
        package = next(
            p
            for p in locked["package"]
            if canonicalize_name(p["name"]) == name and p["version"] == version
        )
        candidates = []
        for wheel in package.get("wheels", []):
            filename = wheel["url"].rsplit("/", 1)[-1]
            matches = [
                tags[tag] for tag in parse_wheel_filename(filename)[3] if tag in tags
            ]
            if matches:
                candidates.append((min(matches), filename, wheel))
        if not candidates:
            raise release.ReleaseBuildError("The frozen runtime wheel is unavailable.")
        _, filename, wheel = min(candidates)
        wheels.append(
            {
                "name": name,
                "version": version,
                "filename": filename,
                "url": wheel["url"],
                "byteLength": wheel["size"],
                "sha256": wheel["hash"],
            }
        )
    return {
        "schemaVersion": 1,
        "python": release._runtime_lock()["python"],
        "platform": "windows-x86_64-cp312",
        "wheels": sorted(wheels, key=lambda w: w["name"]),
        "native": [native],
    }


def convert_stage(staging: Path, plugin_root: Path) -> None:
    """Convert the existing verified composite release to the one public layout."""
    from rhino_mcp.managed_bootstrap import (
        ConfigurationV1,
        ProviderLifecycleStatusV1,
        ProviderReadyV2,
        StartupEnvelopeV2,
    )

    canonical, digest = release.canonical_json_bytes, release.sha256_digest
    manifest = json.loads((staging / "manifest.json").read_bytes())
    build_id = manifest["providerBuildId"]
    source_root = staging / "runtime/python/Lib/site-packages/rhino_mcp"
    (staging / "runtime/python/python312._pth").write_text(
        "python312.zip\n.\nLib/site-packages\nimport site\n", encoding="ascii"
    )
    (staging / "manifest.json").unlink()
    (staging / "file-manifest.json").unlink()
    (staging / "SBOM.spdx.json").unlink()
    for name in ("plugin.json",):
        shutil.copyfile(plugin_root / name, staging / name)
    for name in ("pyproject.toml", "uv.lock", "README.md"):
        shutil.copyfile(plugin_root / "provider" / name, staging / name)
    shutil.copytree(plugin_root / "skills", staging / "skills")
    site_packages = source_root.parent
    for relative in ("pyproject.toml", "uv.lock", "packaging/runtime-lock.json"):
        target = site_packages / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(plugin_root / "provider" / relative, target)
    expected_native = json.loads((plugin_root / "plugin.json").read_bytes())[
        "extensions"
    ]["io.github.xiaoyugan666.native"].get("native")
    if expected_native != manifest["native"]:
        raise release.ReleaseBuildError(
            "The built native package differs from the root binding."
        )

    declarations = {
        "startup-envelope.schema.json": StartupEnvelopeV2.model_json_schema(),
        "configuration.schema.json": ConfigurationV1.model_json_schema(),
        "lifecycle.schema.json": {
            "$defs": {
                "Ready": ProviderReadyV2.model_json_schema(),
                "Status": ProviderLifecycleStatusV1.model_json_schema(),
            },
            "anyOf": [{"$ref": "#/$defs/Ready"}, {"$ref": "#/$defs/Status"}],
        },
        "tool-preview.schema.json": ToolPreviewV1.model_json_schema(by_alias=True),
        "risk-review.schema.json": RiskReviewV1.model_json_schema(),
    }
    for name, schema in declarations.items():
        (staging / name).write_bytes(canonical(schema))
    preview, counts = probe_managed_runtime(staging, build_id)
    if counts != {"tools": 70, "resourcesAndTemplates": 4, "prompts": 2}:
        raise release.ReleaseBuildError(
            "The staged MCP census differs from its frozen contract."
        )
    preview = ToolPreviewV1.model_validate(preview).model_dump(
        mode="json", by_alias=True, exclude_none=True
    )
    (staging / "tool-preview.json").write_bytes(canonical(preview))
    sources = [
        {
            "path": p.relative_to(source_root).as_posix(),
            "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
        }
        for p in sorted(source_root.rglob("*.py"))
    ]
    native = manifest["native"]
    risk = RiskReviewV1(
        schemaVersion=1,
        status="pending-review/provider-candidate",
        engineeringAcceptance="not-performed",
        previewDigest=digest(canonical(preview)),
        providerBuildId=build_id,
        sourceDigest=digest(canonical(sources)),
        sourceFiles=[SourceFile(**s) for s in sources],
        buildIdentity=CanonicalBuildIdentity(
            profile="native-build-record-v1", recordPath="contracts/provider-build.json"
        ),
    )
    (staging / "risk-review.json").write_bytes(canonical(risk.model_dump(mode="json")))
    (staging / "provider-build.json").write_bytes(
        canonical({"schemaVersion": 1, "providerBuildId": build_id})
    )
    build_record = json.loads((staging / "contracts/provider-build.json").read_bytes())
    for record in (*build_record["buildInputs"], *build_record["native"]["inputs"]):
        relative = record["path"]
        target = staging / "source-inputs" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(plugin_root / relative, target)
    native_record = {
        "name": "rhino-mcp-bridge",
        "version": manifest["pluginVersion"],
        "path": native["relativePath"],
        "sha256": native["sha256"],
    }
    locked = runtime_inventory(staging, plugin_root, native_record)
    (staging / "runtime-lock.json").write_bytes(canonical(locked))
    packages = [
        {
            "SPDXID": "SPDXRef-" + w["name"],
            "name": w["name"],
            "versionInfo": w["version"],
            "downloadLocation": w["url"],
            "filesAnalyzed": False,
            "licenseConcluded": "NOASSERTION",
            "checksums": [{"algorithm": "SHA256", "checksumValue": w["sha256"][7:]}],
        }
        for w in locked["wheels"]
    ]
    for name, version, location, checksum in (
        ("python", "3.12.10", locked["python"]["url"], locked["python"]["sha256"]),
        ("rhino", manifest["pluginVersion"], "NOASSERTION", build_id),
        (
            native_record["name"],
            native_record["version"],
            "NOASSERTION",
            native_record["sha256"],
        ),
    ):
        packages.append(
            {
                "SPDXID": "SPDXRef-" + name,
                "name": name,
                "versionInfo": version,
                "downloadLocation": location,
                "filesAnalyzed": False,
                "licenseConcluded": "NOASSERTION",
                "checksums": [{"algorithm": "SHA256", "checksumValue": checksum[7:]}],
            }
        )
    (staging / "sbom.spdx.json").write_bytes(
        canonical(
            {
                "spdxVersion": "SPDX-2.3",
                "dataLicense": "CC0-1.0",
                "SPDXID": "SPDXRef-DOCUMENT",
                "name": "rhino",
                "documentNamespace": "https://spdx.org/spdxdocs/rhino-" + build_id[7:],
                "creationInfo": {
                    "created": "2025-06-15T15:06:40Z",
                    "creators": ["Tool: rhino-provider-builder"],
                },
                "packages": packages,
            }
        )
    )


def stage(
    plugin_root: Path,
    output: Path,
    cache: Path,
    *,
    runtime_archive: Path | None = None,
    dotnet_exe: str | None = None,
    uv_exe: str | None = None,
) -> Path:
    release.configure_plugin_root(plugin_root)
    release.validate_root_identity()
    if output.exists():
        raise release.ReleaseBuildError("The artifact output must be fresh.")
    lock = release._runtime_lock()
    if runtime_archive is not None:
        release._verify_runtime_archive(runtime_archive, lock)
    cache.mkdir(parents=True, exist_ok=True)
    runtime = runtime_archive or cache / "python-3.12.10-embed-amd64.zip"
    verified_runtime = release._download_runtime_archive(
        lock, runtime if runtime_archive is not None or runtime.is_file() else None
    )
    if not runtime.is_file():
        shutil.copyfile(verified_runtime, runtime)
    with tempfile.TemporaryDirectory(
        prefix="rhino-stage-", dir=release._safe_ascii_temp_root()
    ) as temporary:
        archive_path = Path(temporary) / release.ARCHIVE_NAME
        release.build_release(
            archive_path, runtime_archive=runtime, dotnet_exe=dotnet_exe, uv_exe=uv_exe
        )
        staging = output / "artifact"
        staging.mkdir(parents=True)
        with zipfile.ZipFile(archive_path) as archive:
            archive.extractall(staging)
        convert_stage(staging, plugin_root)
    return staging


def failure_code(error: Exception) -> str:
    """Identify a build-owned phase, never exception values or subprocess output."""
    if isinstance(error, release.ReleaseBuildError) and str(error) == (
        "The built native package differs from the root binding."
    ):
        return "nativeBindingMismatch"
    phases = {
        "_download_runtime_archive": "runtimeDownloadFailed",
        "_verify_runtime_archive": "runtimeIntegrityFailed",
        "_install_runtime_dependencies": "runtimeDependenciesFailed",
        "_build_identity": "buildIdentityFailed",
        "_build_native_outputs": "nativeBuildFailed",
        "_build_native_capability_probe": "nativeProbeBuildFailed",
        "_native_capability_digest": "nativeProbeFailed",
        "convert_stage": "stageConversionFailed",
    }
    owners = {Path(__file__).resolve(), Path(release.__file__).resolve()}
    result = "providerStageFailed"
    frame = error.__traceback__
    while frame is not None:
        code = frame.tb_frame.f_code
        if Path(code.co_filename).resolve() in owners and code.co_name in phases:
            result = phases[code.co_name]
        frame = frame.tb_next
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plugin-root", type=Path, required=True)
    parser.add_argument("--stage-only", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--release-sequence", type=int)
    args = parser.parse_args()
    if args.stage_only:
        stage(args.plugin_root.resolve(), args.output.resolve(), args.cache.resolve())
    else:
        if args.release_sequence is None:
            parser.error("--release-sequence required for artifact build")
        subprocess.run(
            [
                sys.executable,
                "-B",
                str(
                    args.plugin_root.resolve().parents[1] / "scripts/build_artifact.py"
                ),
                str(args.plugin_root.resolve()),
                "--release-sequence",
                str(args.release_sequence),
                "--output",
                str(args.output.resolve()),
                "--cache",
                str(args.cache.resolve()),
            ],
            check=True,
            capture_output=True,
        )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        raise SystemExit("artifactBuildFailed:" + failure_code(error)) from None
