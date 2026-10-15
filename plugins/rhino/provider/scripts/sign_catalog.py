"""Sign the fixed Rhino release catalog using the Rust Host publisher contract."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any
from zipfile import BadZipFile, ZipFile

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


PUBLISHER_KEY_ID = "rhino-publisher-v1"
RELEASE_ORIGIN = "https://github.com/XIAOYUGAN666/rhino-mcp-releases/releases/download"
_STABLE_VERSION = re.compile(
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\Z"
)
_RHINO_VARIANT = "windows-x86_64-rhino-8"
_MAX_MANIFEST_BYTES = 1024 * 1024


def canonical_json(value: Any) -> bytes:
    """Match the Host's recursively sorted, compact UTF-8 JSON representation."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("invalidRhinoRelease")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise ValueError("invalidRhinoRelease")


def _read_release(artifact: Path) -> dict[str, Any]:
    try:
        with ZipFile(artifact) as archive:
            matches = [
                item for item in archive.infolist() if item.filename == "manifest.json"
            ]
            if len(matches) != 1 or matches[0].file_size > _MAX_MANIFEST_BYTES:
                raise ValueError("invalidRhinoRelease")
            value = json.loads(
                archive.read(matches[0]).decode("utf-8", errors="strict"),
                object_pairs_hook=_unique_object,
                parse_constant=_reject_constant,
            )
    except (
        OSError,
        BadZipFile,
        KeyError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as exc:
        raise ValueError("invalidRhinoRelease") from exc
    if not isinstance(value, dict):
        raise ValueError("invalidRhinoRelease")
    return value


def _expected_asset_url(release: dict[str, Any], artifact: Path) -> str:
    package_id = release.get("packageId")
    version = release.get("pluginVersion")
    variant = release.get("artifactVariantId")
    expected_name = f"rhino-{version}-windows-x86_64.zip"
    if (
        package_id != "rhino"
        or not isinstance(version, str)
        or not _STABLE_VERSION.fullmatch(version)
        or variant != _RHINO_VARIANT
        or artifact.name != expected_name
    ):
        raise ValueError("invalidRhinoAsset")
    return f"{RELEASE_ORIGIN}/plugin-rhino-v{version}/{expected_name}"


def _artifact_identity(artifact: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    length = 0
    try:
        with artifact.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                length += len(chunk)
                digest.update(chunk)
    except OSError as exc:
        raise ValueError("invalidRhinoArtifact") from exc
    if length == 0:
        raise ValueError("invalidRhinoArtifact")
    return length, "sha256:" + digest.hexdigest()


def sign(
    artifact: Path,
    private_key: Path,
    *,
    not_before: int,
    expires_at: int,
) -> dict[str, Any]:
    """Build one Rhino-only signed catalog; no URL/tag/name is caller supplied."""
    if (
        type(not_before) is not int
        or type(expires_at) is not int
        or not_before <= 0
        or expires_at <= not_before
    ):
        raise ValueError("invalidValidityWindow")
    artifact = artifact.resolve(strict=True)
    release = _read_release(artifact)
    url = _expected_asset_url(release, artifact)
    try:
        loaded_key = serialization.load_pem_private_key(
            private_key.resolve(strict=True).read_bytes(), password=None
        )
    except (OSError, ValueError, TypeError) as exc:
        raise ValueError("invalidSigningKey") from exc
    if not isinstance(loaded_key, Ed25519PrivateKey):
        raise ValueError("invalidSigningKey")

    byte_length, archive_digest = _artifact_identity(artifact)
    signed: dict[str, Any] = {
        "release": release,
        "archive": {
            "url": url,
            "byteLength": byte_length,
            "sha256": archive_digest,
        },
        "publisherKeyId": PUBLISHER_KEY_ID,
        "notBefore": not_before,
        "expiresAt": expires_at,
        "revoked": False,
    }
    payload = canonical_json({"signatureSchemaVersion": 1, **signed})
    signed["signature"] = loaded_key.sign(payload).hex()
    return {"catalogSchemaVersion": 1, "releases": [signed]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--private-key", required=True, type=Path)
    parser.add_argument("--not-before", required=True, type=int)
    parser.add_argument("--expires-at", required=True, type=int)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args(argv)
    try:
        catalog = sign(
            arguments.artifact,
            arguments.private_key,
            not_before=arguments.not_before,
            expires_at=arguments.expires_at,
        )
        payload = canonical_json(catalog)
        with arguments.output.open("xb") as output:
            output.write(payload)
            output.flush()
    except (OSError, ValueError, BadZipFile):
        print("catalogSigningFailed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
