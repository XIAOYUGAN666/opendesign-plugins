"""Internal-only staged tree builder. No archive/signing/Stable publication.

Uses the reviewed embedded CPython + uv frozen wheel inventory algorithm.
Each download is length/hash checked and archives are traversal/collision checked.
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
import urllib.request
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from packaging.requirements import Requirement
from packaging.tags import sys_tags
from packaging.utils import canonicalize_name, parse_wheel_filename
from fixture_engineering.managed_bootstrap import ConfigurationV1, ConnectorExecutionStatusV1, RiskReviewV1, ToolPreviewV1, connector_build_id
from fixture_engineering._connector_control import bootstrap_schema, lifecycle_schema

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


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError("unexpectedRedirect")


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
    target = cache / Path(url.path).name
    if not target.exists():
        temporary = cache / f"{uuid.uuid4()}.download"
        try:
            with (
                urllib.request.build_opener(NoRedirect).open(
                    record["url"], timeout=60
                ) as response,
                temporary.open("xb") as output,
            ):
                remaining = record["byteLength"]
                while chunk := response.read(65536):
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


def canonical(value) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


async def typed_declarations() -> list[dict]:
    from fastmcp import Client
    from fixture_engineering.managed_bootstrap import build_mcp

    async with Client(build_mcp("ci_" + "0" * 32, None, mode="metadata")) as client:
        return sorted(
            [
                tool.model_dump(mode="json", by_alias=True, exclude_none=True)
                for tool in await client.list_tools()
            ],
            key=lambda tool: tool["name"],
        )


def build_fixture(plugin_root: Path, output_dir: Path) -> Path:
    plugin_root = plugin_root.resolve()
    if plugin_root / "connector" != ROOT:
        raise ValueError("fixtureRootMismatch")
    identity = json.loads((plugin_root / "plugin.json").read_text("utf-8"))
    project = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"]
    native = identity["extensions"]["io.github.xiaoyugan666.native"]
    if (
        identity["name"] != project["name"]
        or identity["version"] != project["version"]
        or native["adapter"]
        != {"distribution": project["name"], "version": project["version"]}
    ):
        raise ValueError("fixtureIdentityMismatch")
    locked = create_lock()
    output_dir.mkdir(parents=True, exist_ok=False)
    staged = output_dir / "artifact"
    interpreter = staged / "runtime/python"
    interpreter.mkdir(parents=True)
    cache = ROOT / "cache/downloads"
    cache.mkdir(parents=True, exist_ok=True)
    extract_verified_zip(download(locked["python"], cache), interpreter)
    site_packages = interpreter / "Lib/site-packages"
    for wheel in locked["wheels"]:
        extract_verified_zip(download(wheel, cache), site_packages, wheel=True)
    (interpreter / "python312._pth").write_text(
        "python312.zip\n.\nLib/site-packages\nimport site\n", encoding="utf-8"
    )
    for source in (ROOT / "fixture_engineering").rglob("*.py"):
        target = site_packages / source.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    for name in ("pyproject.toml", "uv.lock"):
        shutil.copyfile(ROOT / name, staged / name)
    shutil.copyfile(plugin_root / "plugin.json", staged / "plugin.json")
    if (plugin_root / "skills").exists():
        shutil.copytree(plugin_root / "skills", staged / "skills")
    (staged / "runtime-lock.json").write_bytes(canonical(locked))
    (staged / "startup-envelope.schema.json").write_bytes(
        canonical(bootstrap_schema())
    )
    (staged / "configuration.schema.json").write_bytes(
        canonical(ConfigurationV1.model_json_schema())
    )
    (staged / "execution-status.schema.json").write_bytes(canonical(lifecycle_schema(ConnectorExecutionStatusV1.model_json_schema())))
    preview = ToolPreviewV1(normalizationVersion=1, tools=asyncio.run(typed_declarations())).model_dump(mode="json", by_alias=True, exclude_none=True)
    (staged / "tool-preview.schema.json").write_bytes(canonical(ToolPreviewV1.model_json_schema(by_alias=True)))
    (staged / "tool-preview.json").write_bytes(canonical(preview))
    build_id = connector_build_id(site_packages / "fixture_engineering")
    source_root = site_packages / "fixture_engineering"
    source_files = [{"path": p.relative_to(source_root).as_posix(), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(source_root.rglob("*.py"))]
    risk = RiskReviewV1(schemaVersion=1, status="pending-review/internal-fixture-only", engineeringAcceptance="not-performed", previewDigest=digest(canonical(preview)), connectorBuildId=build_id, sourceDigest=digest(canonical(source_files)), sourceFiles=source_files)
    (staged / "risk-review.json").write_bytes(canonical(risk.model_dump(mode="json")))
    (staged / "risk-review.schema.json").write_bytes(canonical(RiskReviewV1.model_json_schema()))
    (staged / "fixture-build.json").write_bytes(
        canonical(
            {
                "schemaVersion": 1,
                "connectorBuildId": build_id,
                "toolCatalogDigest": digest(canonical(preview)),
            }
        )
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
    packages.extend([
        {"SPDXID": "SPDXRef-python", "name": "python", "versionInfo": "3.12.10", "downloadLocation": locked["python"]["url"], "filesAnalyzed": False, "licenseConcluded": "NOASSERTION", "checksums": [{"algorithm": "SHA256", "checksumValue": locked["python"]["sha256"][7:]}]},
        {"SPDXID": "SPDXRef-connector", "name": identity["name"], "versionInfo": identity["version"], "downloadLocation": "NOASSERTION", "filesAnalyzed": False, "licenseConcluded": "NOASSERTION", "checksums": [{"algorithm": "SHA256", "checksumValue": build_id[7:]}]},
    ])
    (staged / "sbom.spdx.json").write_bytes(
        canonical(
            {
                "spdxVersion": "SPDX-2.3",
                "dataLicense": "CC0-1.0",
                "SPDXID": "SPDXRef-DOCUMENT",
                "name": identity["name"],
                "documentNamespace": "https://example.invalid/internal-fixture/"
                + build_id[7:],
                "creationInfo": {
                    "created": "2026-10-14T00:00:00Z",
                    "creators": ["Tool: internal-fixture-builder"],
                },
                "packages": packages,
            }
        )
    )
    (staged / "NOTICES.txt").write_text(
        "Internal synthetic fixture only. See runtime/python/LICENSE.txt and installed distribution licenses.\n",
        encoding="utf-8",
    )
    # T07 owns archive/receipt signing; return immutable-input staged tree only.
    return staged


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(build_fixture(ROOT.parent, args.output.resolve()))


if __name__ == "__main__":
    main()
