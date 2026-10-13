"""Canonical Ed25519 helper and independently verified release signing CLI."""
import argparse
from pathlib import Path
import sys

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

try:
    from .verify_artifact import canonical, read_json, verify_artifact
except ImportError:
    from verify_artifact import canonical, read_json, verify_artifact


def sign_envelope(payload: dict[str, object], signer: Ed25519PrivateKey) -> dict[str, object]:
    if not isinstance(signer, Ed25519PrivateKey) or "signature" in payload or "signatureSchemaVersion" in payload:
        raise ValueError("invalidSigningInput")
    signature = signer.sign(canonical({"signatureSchemaVersion": 1, **payload}))
    if len(signature) != 64:
        raise ValueError("invalidSignature")
    return {**payload, "signature": signature.hex()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("schema_bundle", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        candidate_bytes = args.candidate.read_bytes()
        # No key is read before the immutable artifact is independently checked.
        verify_artifact(args.archive, args.candidate, args.schema_bundle, unsigned_candidate=True)
        if args.candidate.read_bytes() != candidate_bytes:
            raise ValueError("candidateChangedDuringVerification")
        raw = sys.stdin.buffer.read(33)
        if len(raw) != 32:
            raise ValueError("invalidSigningKey")
        signer = Ed25519PrivateKey.from_private_bytes(raw)
        result = sign_envelope(read_json(candidate_bytes), signer)
        with args.output.open("xb") as stream:
            stream.write(canonical(result))
    except Exception:
        parser.exit(1, "envelopeSigningFailed\n")


if __name__ == "__main__":
    main()
