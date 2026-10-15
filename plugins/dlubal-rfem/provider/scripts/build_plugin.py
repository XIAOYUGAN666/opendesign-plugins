"""Build an unsigned review artifact from frozen, hash-verified Windows inputs.

Signing/publication are separate explicit release steps. This tool never reads
credentials, mutates a previous build, or imports the legacy Provider.
"""

import argparse
import asyncio
import hashlib
import json
import shutil
import subprocess
import sys
import tomllib
import urllib.parse
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath

import httpx
from packaging.requirements import Requirement
from packaging.tags import sys_tags
from packaging.utils import canonicalize_name, parse_wheel_filename

from dlubal_rfem_mcp.core.catalog import canonical_json
from dlubal_rfem_mcp.core.config import (
    ConfigurationV1,
    ProviderLifecycleStatusV1,
    ProviderReadyV2,
    StartupEnvelopeV2,
)
from dlubal_rfem_mcp.core.identity import provider_build_id, provider_build_manifest
from dlubal_rfem_mcp.core.release import RiskReviewV1, ToolPreviewV1

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = {
    "filename": "python-3.12.10-embed-amd64.zip",
    "url": "https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip",
    "byteLength": 11133606,
    "sha256": "sha256:4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3",
    "checksumSource": "https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip.spdx.json",
}


def digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def verify_download(path: Path, record: dict) -> None:
    if path.stat().st_size != record["byteLength"]:
        raise ValueError("artifactDigestMismatch")
    with path.open("rb") as stream:
        actual = "sha256:" + hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != record["sha256"]:
        raise ValueError("artifactDigestMismatch")


def download(record: dict, cache: Path) -> Path:
    url = urllib.parse.urlsplit(record["url"])
    if (
        url.scheme != "https"
        or url.hostname not in {"www.python.org", "files.pythonhosted.org"}
        or url.username
        or url.password
        or url.query
        or url.fragment
    ):
        raise ValueError("invalidDownloadOrigin")
    name = Path(url.path).name
    target = cache / name
    if not target.exists():
        temporary = cache / f"{uuid.uuid4()}.download"
        try:
            with (
                httpx.stream(
                    "GET", record["url"], timeout=60, follow_redirects=False
                ) as response,
                temporary.open("xb") as output,
            ):
                response.raise_for_status()
                if str(response.url) != record["url"]:
                    raise ValueError("unexpectedRedirect")
                remaining = record["byteLength"]
                for chunk in response.iter_bytes(65536):
                    remaining -= len(chunk)
                    if remaining < 0:
                        raise ValueError("artifactDigestMismatch")
                    output.write(chunk)
            verify_download(temporary, record)
            temporary.rename(target)
        finally:
            temporary.unlink(missing_ok=True)
    verify_download(target, record)
    return target


def extract_verified_zip(
    source: Path, destination: Path, *, wheel=False, replace_existing=False
) -> None:
    seen = set()
    with zipfile.ZipFile(source) as archive:
        for entry in archive.infolist():
            name = entry.orig_filename.rstrip("/")
            parts = name.split("/")
            if (
                not name
                or "\\" in name
                or ":" in name
                or name.startswith("/")
                or any(part in {"", ".", ".."} for part in parts)
            ):
                raise ValueError("unsafeArchive")
            if (
                name.lower() in seen
                or (entry.external_attr >> 16) & 0o170000 == 0o120000
            ):
                raise ValueError("unsafeArchive")
            seen.add(name.lower())
            relative = PurePosixPath(name)
            # Wheel installation scheme: scripts are not part of this managed service.
            if wheel and parts[0].endswith(".data"):
                if len(parts) < 3:
                    if entry.is_dir():
                        continue
                    raise ValueError("invalidWheelLayout")
                if parts[1] in {"scripts", "headers"}:
                    continue
                if parts[1] not in {"purelib", "platlib", "data"}:
                    raise ValueError("invalidWheelLayout")
                relative = PurePosixPath(*parts[2:])
            if relative.suffix.lower() in {".exe", ".com", ".bat", ".cmd", ".ps1"} and (
                wheel
                or relative.as_posix()
                not in {"python.exe", "runtime/python/python.exe"}
            ):
                continue
            if "__pycache__" in relative.parts or relative.suffix == ".pyc":
                continue
            target = destination.joinpath(*relative.parts)
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if entry.file_size > 256 * 1024 * 1024:
                raise ValueError("oversizedArchiveMember")
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(entry) as input_stream:
                content = input_stream.read(entry.file_size + 1)
            if len(content) != entry.file_size:
                raise ValueError("unsafeArchive")
            if target.exists():
                if target.read_bytes() != content:
                    if not replace_existing:
                        raise ValueError("wheelFileCollision")
                    target.write_bytes(content)
                continue
            with target.open("xb") as output:
                output.write(content)


def create_lock() -> dict:
    if sys.platform != "win32" or sys.version_info[:3] != (3, 12, 10):
        raise ValueError("buildRequiresFrozenWindowsPython")
    exported = subprocess.check_output(
        [
            "uv",
            "export",
            "--frozen",
            "--no-dev",
            "--no-emit-project",
            "--no-hashes",
            "--format",
            "requirements-txt",
        ],
        cwd=ROOT,
        text=True,
    )
    resolved = []
    for line in exported.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        requirement = Requirement(line)
        if requirement.marker is None or requirement.marker.evaluate():
            resolved.append(requirement)
    lock = tomllib.loads((ROOT / "uv.lock").read_text("utf-8"))
    tags = {tag: index for index, tag in enumerate(sys_tags())}
    wheels = []
    for requirement in resolved:
        package = next(
            package
            for package in lock["package"]
            if canonicalize_name(package["name"]) == canonicalize_name(requirement.name)
            and package["version"] in requirement.specifier
        )
        candidates = []
        for wheel in package.get("wheels", []):
            filename = Path(urllib.parse.urlsplit(wheel["url"]).path).name
            wheel_tags = parse_wheel_filename(filename)[3]
            priorities = [tags[tag] for tag in wheel_tags if tag in tags]
            if priorities:
                candidates.append((min(priorities), filename, wheel))
        if not candidates:
            raise ValueError(f"missingFrozenWheel:{package['name']}")
        _, filename, wheel = min(candidates)
        wheels.append(
            {
                "name": package["name"],
                "version": package["version"],
                "filename": filename,
                "url": wheel["url"],
                "sha256": wheel["hash"],
                "byteLength": wheel["size"],
            }
        )
    return {
        "schemaVersion": 1,
        "python": RUNTIME,
        "platform": "windows-x86_64-cp312",
        "wheels": sorted(wheels, key=lambda item: item["name"]),
    }


async def typed_declarations():
    from fastmcp import Client

    from dlubal_rfem_mcp.core.server import build_mcp

    envelope = StartupEnvelopeV2.model_validate(
        {
            "schemaVersion": 2,
            "mode": "metadata",
            "connectorInstanceId": "ci_" + "0" * 32,
            "generation": 1,
            "bind": {"host": "127.0.0.1", "port": 8765, "path": "/mcp"},
            "bearerToken": "b" * 64,
            "providerBuildId": provider_build_id(),
        }
    )
    async with Client(build_mcp(envelope)) as client:
        return sorted(
            [
                tool.model_dump(mode="json", by_alias=True, exclude_none=True)
                for tool in await client.list_tools()
            ],
            key=lambda tool: tool["name"],
        )


def stage(plugin_root: Path, output: Path, cache: Path) -> Path:
    plugin_root = plugin_root.resolve(strict=True)
    if plugin_root / "provider" != ROOT:
        raise ValueError("providerRootMismatch")
    identity = json.loads((plugin_root / "plugin.json").read_text("utf-8"))
    project = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"]
    if identity["name"] != "dlubal-rfem" or identity["version"] != project["version"]:
        raise ValueError("providerRootVersionMismatch")
    locked = create_lock()
    if locked != json.loads((ROOT / "packaging/runtime-lock.json").read_text("utf-8")):
        raise ValueError("runtimeLockDrift")
    output.mkdir(parents=True, exist_ok=False)
    cache.mkdir(parents=True, exist_ok=True)
    staging = output / "artifact"
    interpreter = staging / "runtime/python"
    interpreter.mkdir(parents=True)
    extract_verified_zip(download(locked["python"], cache), interpreter)
    site_packages = interpreter / "Lib/site-packages"
    with ThreadPoolExecutor(max_workers=4) as pool:
        archives = list(
            pool.map(lambda wheel: download(wheel, cache), locked["wheels"])
        )
    for item, archive in zip(locked["wheels"], archives, strict=True):
        if item["name"] != "dlubal-api":
            extract_verified_zip(archive, site_packages, wheel=True)
    adapter = next(
        archive
        for item, archive in zip(locked["wheels"], archives, strict=True)
        if item["name"] == "dlubal-api"
    )
    extract_verified_zip(adapter, site_packages, wheel=True, replace_existing=True)
    (interpreter / "python312._pth").write_text(
        "python312.zip\n.\nLib/site-packages\nimport site\n", encoding="utf-8"
    )
    for source in (ROOT / "dlubal_rfem_mcp").rglob("*.py"):
        target = site_packages / source.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    for relative in (
        "pyproject.toml",
        "uv.lock",
        "packaging/runtime-lock.json",
        "packaging/release-input.json",
    ):
        target = site_packages / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    for name in ("pyproject.toml", "uv.lock", "ORIGIN.md"):
        shutil.copyfile(ROOT / name, staging / name)
    shutil.copyfile(plugin_root / "plugin.json", staging / "plugin.json")
    shutil.copytree(plugin_root / "skills", staging / "skills")
    shutil.copyfile(ROOT / "README.md", staging / "README.md")
    (staging / "runtime-lock.json").write_bytes(canonical_json(locked))
    declarations = {
        "startup-envelope.schema.json": StartupEnvelopeV2.model_json_schema(),
        "configuration.schema.json": ConfigurationV1.model_json_schema(by_alias=True),
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
    for name, declaration in declarations.items():
        (staging / name).write_bytes(canonical_json(declaration))
    preview = {"normalizationVersion": 1, "tools": asyncio.run(typed_declarations())}
    if len(preview["tools"]) != 8:
        raise ValueError("incompleteToolCatalog")
    (staging / "tool-preview.json").write_bytes(canonical_json(preview))
    build_id = provider_build_id(site_packages / "dlubal_rfem_mcp")
    source_root = site_packages / "dlubal_rfem_mcp"
    sources = [
        {
            "path": p.relative_to(source_root).as_posix(),
            "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
        }
        for p in sorted(source_root.rglob("*.py"))
    ]
    risk = RiskReviewV1(
        schemaVersion=1,
        status="pending-review/provider-candidate",
        engineeringAcceptance="not-performed",
        previewDigest=digest(canonical_json(preview)),
        providerBuildId=build_id,
        sourceDigest=digest(canonical_json(sources)),
        sourceFiles=sources,
        buildIdentity={
            "profile": "input-manifest-v1",
            "files": provider_build_manifest(source_root),
        },
    )
    (staging / "risk-review.json").write_bytes(
        canonical_json(risk.model_dump(mode="json"))
    )
    (staging / "provider-build.json").write_bytes(
        canonical_json({"schemaVersion": 1, "providerBuildId": build_id})
    )
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
    packages.extend(
        [
            {
                "SPDXID": "SPDXRef-python",
                "name": "python",
                "versionInfo": "3.12.10",
                "downloadLocation": locked["python"]["url"],
                "filesAnalyzed": False,
                "licenseConcluded": "NOASSERTION",
                "checksums": [
                    {
                        "algorithm": "SHA256",
                        "checksumValue": locked["python"]["sha256"][7:],
                    }
                ],
            },
            {
                "SPDXID": "SPDXRef-provider",
                "name": identity["name"],
                "versionInfo": identity["version"],
                "downloadLocation": "NOASSERTION",
                "filesAnalyzed": False,
                "licenseConcluded": "NOASSERTION",
                "checksums": [{"algorithm": "SHA256", "checksumValue": build_id[7:]}],
            },
        ]
    )
    (staging / "sbom.spdx.json").write_bytes(
        canonical_json(
            {
                "spdxVersion": "SPDX-2.3",
                "dataLicense": "CC0-1.0",
                "SPDXID": "SPDXRef-DOCUMENT",
                "name": identity["name"],
                "documentNamespace": "https://github.com/XIAOYUGAN666/opendesign-plugins/sbom/"
                + build_id[7:],
                "creationInfo": {
                    "created": "2026-10-15T00:00:00Z",
                    "creators": ["Tool: rfem-provider-builder"],
                },
                "packages": packages,
            }
        )
    )
    (staging / "NOTICES.txt").write_text(
        "Dlubal RFEM Provider. See ORIGIN.md, runtime/python/LICENSE.txt and installed distribution licenses.\n",
        encoding="utf-8",
    )
    return staging


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--lock", action="store_true")
    parser.add_argument("--plugin-root", type=Path, default=ROOT.parent)
    parser.add_argument("--stage-only", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--cache", type=Path)
    args = parser.parse_args()
    if args.lock:
        (ROOT / "packaging/runtime-lock.json").write_bytes(
            canonical_json(create_lock())
        )
        return
    if not args.output or not args.cache:
        parser.error("--output and --cache required")
    if args.stage_only:
        print(stage(args.plugin_root, args.output.resolve(), args.cache.resolve()))
    else:
        public_builder = (
            args.plugin_root.resolve().parents[1] / "scripts/build_artifact.py"
        )
        subprocess.run(
            [
                sys.executable,
                "-B",
                str(public_builder),
                str(args.plugin_root.resolve()),
                "--output",
                str(args.output.resolve()),
                "--cache",
                str(args.cache.resolve()),
            ],
            check=True,
        )


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as error:
        raise SystemExit(error.returncode) from None
    except Exception:  # noqa: BLE001 - CLI errors cannot expose commands or local paths
        raise SystemExit("artifactBuildFailed") from None
