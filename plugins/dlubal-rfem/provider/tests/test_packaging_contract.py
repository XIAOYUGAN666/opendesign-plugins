import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from dlubal_rfem_mcp.core.catalog import canonical_json
from scripts.build_plugin import extract_verified_zip, verify_download
from scripts.sign_catalog import sign


def test_public_builder_accepts_explicit_plugin_root():
    result = subprocess.run(
        [sys.executable, "-B", "-m", "scripts.build_plugin", "--help"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "--plugin-root" in result.stdout


def test_public_provider_builder_preserves_safe_parent_error_without_traceback():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "scripts.build_plugin",
            "--plugin-root",
            str(root.parent),
            "--output",
            str(root),
            "--cache",
            str(root / "cache/downloads"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "artifactBuildFailed:outputMustBeFreshAndOutsideRepository" in result.stderr
    assert "Traceback" not in result.stderr
    assert str(root) not in result.stderr


def test_public_provider_verifier_never_exposes_input_path(tmp_path):
    archive = tmp_path / "synthetic-private-name.zip"
    archive.write_bytes(b"")
    result = subprocess.run(
        [sys.executable, "-B", "-m", "scripts.verify_plugin", str(archive)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert (
        "Traceback" not in result.stderr
        and "synthetic-private-name" not in result.stderr
    )


def test_runtime_download_requires_length_and_digest(tmp_path: Path):
    archive = tmp_path / "runtime.zip"
    archive.write_bytes(b"incomplete")
    with pytest.raises(ValueError, match="artifactDigestMismatch"):
        verify_download(archive, {"byteLength": 999, "sha256": "sha256:" + "0" * 64})


@pytest.mark.parametrize(
    "name", ["../escape", "/absolute", "C:/outside", "a\\b", "a:stream", "a/../b"]
)
def test_builder_rejects_archive_escape(tmp_path: Path, name: str):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        entry = zipfile.ZipInfo("placeholder")
        entry.filename = (
            name  # Preserve backslashes that ZipInfo normalizes on Windows.
        )
        archive.writestr(entry, "untrusted")
    source = tmp_path / "input.zip"
    source.write_bytes(data.getvalue())
    with pytest.raises(ValueError, match="unsafeArchive"):
        extract_verified_zip(source, tmp_path / "output")


def test_wheel_overlay_accepts_identical_bytes_and_rejects_drift(tmp_path: Path):
    def wheel(content: bytes, filename: str) -> Path:
        source = tmp_path / filename
        with zipfile.ZipFile(source, "w") as archive:
            archive.writestr("shared/module.py", content)
        return source

    output = tmp_path / "output"
    extract_verified_zip(wheel(b"same", "one.whl"), output, wheel=True)
    extract_verified_zip(wheel(b"same", "two.whl"), output, wheel=True)
    with pytest.raises(ValueError, match="wheelFileCollision"):
        extract_verified_zip(wheel(b"different", "three.whl"), output, wheel=True)
    extract_verified_zip(
        wheel(b"adapter-owned", "adapter.whl"),
        output,
        wheel=True,
        replace_existing=True,
    )
    assert (output / "shared/module.py").read_bytes() == b"adapter-owned"


def test_catalog_signing_uses_external_ed25519_key_and_exact_asset_name(tmp_path: Path):
    release = {"pluginVersion": "0.1.0"}
    artifact = tmp_path / (
        "dlubal-rfem-0.1.0-windows-x86_64-rfem-6.14.0013.249-dlubal-api-2.14.13.zip"
    )
    with zipfile.ZipFile(artifact, "w") as archive:
        archive.writestr("release.json", json.dumps(release))
    private = Ed25519PrivateKey.generate()
    key_path = tmp_path / "publisher.pem"
    key_path.write_bytes(
        private.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )

    catalog = sign(artifact, key_path, "publisher-v1", 100, 200)

    signed = catalog["releases"][0]
    assert signed["publisherKeyId"] == "publisher-v1"
    assert signed["archive"]["url"].endswith("/" + artifact.name)
    assert len(signed["signature"]) == 128
    assert "publisher.pem" not in json.dumps(catalog)
    payload = canonical_json(
        {
            "signatureSchemaVersion": 1,
            **{key: value for key, value in signed.items() if key != "signature"},
        }
    )
    private.public_key().verify(bytes.fromhex(signed["signature"]), payload)
