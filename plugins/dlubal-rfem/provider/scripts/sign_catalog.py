"""Create the canonical signed catalog from a verified immutable artifact."""

import argparse
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import load_pem_private_key

from dlubal_rfem_mcp.core.catalog import canonical_json


def sign(
    artifact: Path,
    private_key: Path,
    key_id: str,
    not_before: int,
    expires_at: int,
) -> dict:
    with ZipFile(artifact) as archive:
        release = json.loads(archive.read("release.json"))
    archive_bytes = artifact.read_bytes()
    signed = {
        "release": release,
        "archive": {
            "url": "https://github.com/XIAOYUGAN666/opendesign-plugin-releases/releases/download/"
            f"plugin-dlubal-rfem-v{release['pluginVersion']}/{artifact.name}",
            "byteLength": len(archive_bytes),
            "sha256": "sha256:" + hashlib.sha256(archive_bytes).hexdigest(),
        },
        "publisherKeyId": key_id,
        "notBefore": not_before,
        "expiresAt": expires_at,
        "revoked": False,
    }
    payload = canonical_json({"signatureSchemaVersion": 1, **signed})
    key = load_pem_private_key(private_key.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise TypeError("invalidSigningKey")
    signature = key.sign(payload)
    if len(signature) != 64:
        raise ValueError("invalidSigningKey")
    signed["signature"] = signature.hex()
    return {"catalogSchemaVersion": 1, "releases": [signed]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--private-key", required=True, type=Path)
    parser.add_argument("--key-id", required=True)
    parser.add_argument("--not-before", required=True, type=int)
    parser.add_argument("--expires-at", required=True, type=int)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    if arguments.not_before <= 0 or arguments.expires_at <= arguments.not_before:
        raise ValueError("invalidValidityWindow")
    arguments.output.write_bytes(
        canonical_json(
            sign(
                arguments.artifact.resolve(),
                arguments.private_key.resolve(),
                arguments.key_id,
                arguments.not_before,
                arguments.expires_at,
            )
        )
    )


if __name__ == "__main__":
    main()
