"""Independently verify the bounded bytes of a skill-only release candidate."""
import argparse
import stat
import sys
import time
import zipfile
from pathlib import Path

try:
    from .build_skill_artifact import validate_files
    from .verify_artifact import canonical, digest, read_json, safe_path
except ImportError:
    from build_skill_artifact import validate_files
    from verify_artifact import canonical, digest, read_json, safe_path


def verify(archive, receipt, *, trusted_keys=None, unsigned=False):
    size = archive.stat().st_size
    if size <= 0 or size > 16 * 1024 * 1024:
        raise ValueError("archiveMismatch")
    with archive.open("rb") as stream:
        raw = stream.read(size + 1)
    if len(raw) != receipt["archive"]["byteLength"] or digest(raw) != receipt["archive"]["sha256"]:
        raise ValueError("archiveMismatch")
    if not unsigned:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        key = (trusted_keys or {}).get(receipt["publisherKeyId"])
        now = int(time.time())
        if key is None or receipt["revoked"] or not receipt["notBefore"] <= now < receipt["expiresAt"]:
            raise ValueError("untrustedRelease")
        signature = bytes.fromhex(receipt["signature"])
        Ed25519PublicKey.from_public_bytes(key).verify(signature, canonical({"signatureSchemaVersion": 1, **{key: value for key, value in receipt.items() if key != "signature"}}))
    files, folded, total = {}, set(), 0
    import io
    with zipfile.ZipFile(io.BytesIO(raw)) as zipped:
        entries = zipped.infolist()
        if not 0 < len(entries) <= 256:
            raise ValueError("invalidInventory")
        for entry in entries:
            name = entry.orig_filename
            safe_path(name)
            mode = entry.external_attr >> 16
            if name.casefold() in folded or not stat.S_ISREG(mode) or entry.flag_bits & 1:
                raise ValueError("invalidInventory")
            total += entry.file_size
            if entry.file_size > 8 * 1024 * 1024 or total > 8 * 1024 * 1024:
                raise ValueError("invalidInventory")
            folded.add(name.casefold())
            files[name] = zipped.read(entry)
    files = dict(sorted(files.items()))
    identity = validate_files(files)
    release = receipt["release"]
    inventory = [{"path": name, "byteLength": len(content), "sha256": digest(content)} for name, content in files.items()]
    if identity["name"] != release["packageId"] or identity["version"] != release["pluginVersion"] or digest(canonical(inventory)) != release["filesDigest"] or type(release["releaseSequence"]) is not int or not 0 < release["releaseSequence"] < 2**64:
        raise ValueError("releaseMismatch")
    return {"archiveDigest": digest(raw), "filesDigest": release["filesDigest"], "verification": "unsigned-candidate" if unsigned else "signed-receipt"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("receipt", type=Path)
    parser.add_argument("--unsigned", action="store_true")
    args = parser.parse_args()
    try:
        print(canonical(verify(args.archive, read_json(args.receipt.read_bytes()), unsigned=args.unsigned)).decode())
    except Exception:
        parser.exit(1, "skillArtifactVerificationFailed\n")


if __name__ == "__main__":
    main()
