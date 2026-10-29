"""The Rhino release signer must emit the exact Rust Host publisher contract."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest

from sign_catalog import canonical_json, sign


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = PROJECT_ROOT / "tests" / "fixtures"
ASSET_URL = (
    "https://github.com/XIAOYUGAN666/rhino-mcp-releases/releases/download/"
    "plugin-rhino-v0.1.0/rhino-0.1.0-windows-x86_64.zip"
)


def _artifact(path: Path, *, package_id: str = "rhino") -> bytes:
    release = json.loads(
        (FIXTURE_ROOT / "release_manifest_v1.json").read_text(encoding="utf-8")
    )
    release["packageId"] = package_id
    release["software"]["product"] = package_id
    with ZipFile(path, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for name, payload in (
            ("manifest.json", canonical_json(release)),
            ("file-manifest.json", b"fixed test artifact"),
        ):
            info = ZipInfo(name, date_time=(2025, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.create_system = 0
            info.external_attr = 0
            info.extra = b""
            info.comment = b""
            archive.writestr(info, payload, compresslevel=9)
    return path.read_bytes()


def _private_key_file(path: Path) -> Ed25519PrivateKey:
    key = Ed25519PrivateKey.from_private_bytes(bytes([7]) * 32)
    path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    return key


def test_catalog_signature_matches_rust_publisher_payload(tmp_path: Path) -> None:
    artifact = tmp_path / "rhino-0.1.0-windows-x86_64.zip"
    artifact_bytes = _artifact(artifact)
    key_path = tmp_path / "test-key.pem"
    key = _private_key_file(key_path)

    catalog = sign(
        artifact,
        key_path,
        not_before=1_700_000_000,
        expires_at=1_731_536_000,
    )

    assert set(catalog) == {"catalogSchemaVersion", "releases"}
    assert catalog["catalogSchemaVersion"] == 1
    signed = catalog["releases"][0]
    assert signed["publisherKeyId"] == "rhino-publisher-v1"
    assert signed["archive"] == {
        "url": ASSET_URL,
        "byteLength": len(artifact_bytes),
        "sha256": "sha256:" + hashlib.sha256(artifact_bytes).hexdigest(),
    }
    signature = bytes.fromhex(signed.pop("signature"))
    key.public_key().verify(
        signature,
        canonical_json({"signatureSchemaVersion": 1, **signed}),
    )


def test_signer_rejects_unregistered_package_and_noncanonical_asset_name(
    tmp_path: Path,
) -> None:
    key_path = tmp_path / "test-key.pem"
    _private_key_file(key_path)
    other_package = tmp_path / "rhino-0.1.0-windows-x86_64.zip"
    _artifact(other_package, package_id="other")
    with pytest.raises(ValueError, match="invalidRhinoAsset"):
        sign(other_package, key_path, not_before=100, expires_at=200)

    valid_package = tmp_path / "rhino-0.1.0-windows-x86_64.zip"
    _artifact(valid_package)
    renamed = tmp_path / "custom.zip"
    renamed.write_bytes(valid_package.read_bytes())
    with pytest.raises(ValueError, match="invalidRhinoAsset"):
        sign(renamed, key_path, not_before=100, expires_at=200)
