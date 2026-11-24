"""Prepare the SDK identified by Dlubal's PyPI compatibility metadata."""

import hashlib
import json
import re
import stat
import sys
from importlib.metadata import PathDistribution, version
from pathlib import Path, PurePosixPath
from shutil import copyfileobj
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit
from zipfile import ZipFile

import httpx
from packaging.requirements import Requirement

PYPI = "https://pypi.org/pypi/dlubal.api"
ENVIRONMENT = "rfem-sdk"
_BINDING = "binding.json"


def build_parts(build: str) -> tuple[int, ...]:
    if not re.fullmatch(r"6\.\d{1,3}\.\d{1,4}\.\d{1,6}", build):
        raise ValueError("invalidRfemBuild")
    return tuple(map(int, build.split(".")))


def select_sdk(build: str) -> str:
    _, minor, patch, _ = build_parts(build)
    return f"2.{minor}.{patch}"


def service_version_matches(build: str, reported: str) -> bool:
    if not re.fullmatch(r"6\.\d{1,3}\.\d{1,4}(?:\.\d{1,6})?", reported):
        return False
    parts = tuple(map(int, reported.split(".")))
    return build_parts(build)[: len(parts)] == parts


def _release_record(client: httpx.Client, build: str) -> dict:
    sdk = select_sdk(build)
    response = client.get(f"{PYPI}/{sdk}/json")
    response.raise_for_status()
    release = response.json()
    _, minor, patch, revision = build_parts(build)
    compatibility = (
        rf"(?mi)^\s*✅\s*\*\*X\.{minor}\.0*{patch}\.{revision}\.[a-f0-9]+\*\*\s*$"
    )
    if release["info"]["version"] != sdk or not re.search(
        compatibility, release["info"]["description"]
    ):
        raise ValueError("rfemSdkBuildUnconfirmed")
    expected = f"dlubal_api-{sdk}-py3-none-any.whl"
    (wheel,) = [
        item
        for item in release["urls"]
        if item["filename"] == expected and not item["yanked"]
    ]
    record = {key: wheel[key] for key in ("filename", "url", "size")}
    record["sha256"] = wheel["digests"]["sha256"]
    _validate_record(record, sdk)
    return record


def _validate_record(record: dict, sdk: str) -> None:
    url = urlsplit(record["url"])
    filename = f"dlubal_api-{sdk}-py3-none-any.whl"
    if (
        record["filename"] != filename
        or PurePosixPath(url.path).name != filename
        or url.scheme != "https"
        or url.netloc != "files.pythonhosted.org"
        or url.query
        or url.fragment
        or not re.fullmatch("[a-f0-9]{64}", record["sha256"])
        or type(record["size"]) is not int
        or not 0 < record["size"] <= 128 * 1024 * 1024
    ):
        raise ValueError("invalidRfemSdkDownload")


def _verify_wheel(path: Path, record: dict) -> None:
    if path.stat().st_size != record["size"]:
        raise ValueError("rfemSdkIntegrityFailed")
    with path.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != record["sha256"]:
            raise ValueError("rfemSdkIntegrityFailed")


def _download_wheel(
    client: httpx.Client, record: dict, cache: Path, staging: Path
) -> Path:
    target = cache / record["filename"]
    if target.exists():
        _verify_wheel(target, record)
        return target
    download = staging / record["filename"]
    with client.stream("GET", record["url"]) as response, download.open("xb") as stream:
        response.raise_for_status()
        remaining = record["size"]
        for block in response.iter_bytes(65536):
            remaining -= len(block)
            if remaining < 0:
                raise ValueError("rfemSdkIntegrityFailed")
            stream.write(block)
    _verify_wheel(download, record)
    download.replace(target)
    return target


def _distribution(root: Path, sdk: str) -> PathDistribution:
    return PathDistribution(root / f"dlubal_api-{sdk}.dist-info")


def _check_dependencies(distribution: PathDistribution, sdk: str) -> None:
    if (
        distribution.version != sdk
        or distribution.metadata["Name"].lower().replace("-", ".") != "dlubal.api"
    ):
        raise ValueError("rfemSdkIntegrityFailed")
    for constraint in distribution.requires or []:
        requirement = Requirement(constraint)
        if (
            requirement.marker is None or requirement.marker.evaluate({"extra": ""})
        ) and not (
            requirement.specifier.contains(version(requirement.name), prereleases=True)
        ):
            raise ValueError("rfemSdkDependencyMismatch")


def _extract_wheel(wheel: Path, target: Path, sdk: str) -> None:
    seen = set()
    with ZipFile(wheel) as archive:
        for member in archive.infolist():
            name = member.orig_filename.rstrip("/")
            parts = name.split("/")
            if (
                parts[0] not in {"dlubal", f"dlubal_api-{sdk}.dist-info"}
                or member.is_dir()
            ):
                continue
            if (
                any(
                    part in {"", ".", ".."} or ":" in part or "\\" in part
                    for part in parts
                )
                or name.casefold() in seen
                or stat.S_ISLNK(member.external_attr >> 16)
            ):
                raise ValueError("invalidRfemSdkArchive")
            seen.add(name.casefold())
            destination = target.joinpath(*parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, destination.open("xb") as output:
                copyfileobj(source, output)
    _check_dependencies(_distribution(target, sdk), sdk)
    if not (target / "dlubal/api/rfem/application.py").is_file():
        raise ValueError("invalidRfemSdkArchive")


def prepare_sdk(data: Path, executable: Path, build: str) -> str:
    sdk = select_sdk(build)
    environment = data / ENVIRONMENT
    existing = read_binding(data)
    if existing is not None and (existing["build"], existing["executable"]) == (
        build,
        str(executable),
    ):
        _check_dependencies(_distribution(environment, sdk), sdk)
        return sdk
    if environment.exists():
        raise ValueError("rfemReinstallRequired")
    data.mkdir(parents=True, exist_ok=True)
    cache = data / "sdk-cache"
    cache.mkdir(exist_ok=True)
    record_path = cache / f"{build}.json"
    with TemporaryDirectory(prefix=".rfem-prepare-", dir=data) as temporary:
        staging = Path(temporary)
        with httpx.Client(
            timeout=30, follow_redirects=False, trust_env=False
        ) as client:
            if record_path.is_file():
                record = json.loads(record_path.read_bytes())
                _validate_record(record, sdk)
            else:
                record = _release_record(client, build)
            wheel = _download_wheel(client, record, cache, staging)
        target = staging / ENVIRONMENT
        _extract_wheel(wheel, target, sdk)
        pending = staging / "record.json"
        pending.write_text(json.dumps(record), encoding="utf-8")
        pending.replace(record_path)
        (target / _BINDING).write_text(
            json.dumps(
                {
                    "build": build,
                    "executable": str(executable),
                    "sdkVersion": sdk,
                }
            ),
            encoding="utf-8",
        )
        target.rename(environment)
    return sdk


def read_binding(data: Path) -> dict | None:
    try:
        root = data / ENVIRONMENT
        if root.resolve() != data.resolve() / ENVIRONMENT:
            return None
        binding = json.loads((root / _BINDING).read_bytes())
        sdk = binding["sdkVersion"]
        if sdk == select_sdk(binding["build"]) == _distribution(root, sdk).version:
            return binding
    except (OSError, KeyError, TypeError, ValueError):
        pass
    return None


def activate_sdk(data: Path) -> None:
    sys.path.insert(0, str(data / ENVIRONMENT))
