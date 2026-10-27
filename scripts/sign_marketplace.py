"""Sign verified candidate discovery bytes; private key comes only from stdin."""
import argparse
from pathlib import Path
import sys
import time

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

try:
    from .generate_marketplace import marketplace_document, verified_views
    from .generate_stable_view import REPOSITORY, contract
    from .sign_envelope import sign_envelope
    from .verify_artifact import canonical, digest, read_json
except ImportError:
    from generate_marketplace import marketplace_document, verified_views
    from generate_stable_view import REPOSITORY, contract
    from sign_envelope import sign_envelope
    from verify_artifact import canonical, digest, read_json


def signing_payload(raw, bindings, sequence, views, trust_policy, view_root, artifact_roots, verified_contexts):
    if not trust_policy or trust_policy.get("repositoryIdentity") != REPOSITORY:
        raise ValueError("missingPublicationAuthorization")
    previous = trust_policy.get("previousSequence", 0)
    if type(previous) is not int or not 0 <= previous < 2**64 or type(sequence) is not int or not 0 < sequence < 2**64 or sequence <= previous:
        raise ValueError("marketplaceSequenceRollback")
    expected, identities = verified_views(bindings, view_root, trust_policy, artifact_roots, verified_contexts)
    if views != expected or read_json(raw) != marketplace_document(identities):
        raise ValueError("marketplaceBindingMismatch")
    key_id = trust_policy["marketplaceKeyId"]
    key = trust_policy["keys"][key_id]
    now = int(time.time())
    if key.get("revoked") is not False or any(type(key.get(k)) is not int or not 0 <= key[k] < 2**64 for k in ("notBefore", "expiresAt")) or not 0 < key["notBefore"] <= now < key["expiresAt"]:
        raise ValueError("marketplaceSignerDenied")
    if not {receipt["release"]["packageId"] for receipt in bindings} <= set(key.get("packages", [])):
        raise ValueError("marketplaceSignerScopeDenied")
    not_before = max([key["notBefore"]] + [r["notBefore"] for r in bindings])
    expires_at = min([key["expiresAt"]] + [r["expiresAt"] for r in bindings])
    native = [item for item in bindings if "providerBuildId" in item["release"]]
    skills = [item for item in bindings if "providerBuildId" not in item["release"]]
    result = {"marketplaceSchemaVersion": 1, "repositoryIdentity": REPOSITORY, "sequence": sequence, "marketplaceSha256": digest(raw), "releases": native, "views": views, "publisherKeyId": key_id, "notBefore": not_before, "expiresAt": expires_at}
    if skills:
        result["skillReleases"] = skills
    contract({**result, "signature": ""}, "SignedMarketplaceV1")
    return result


def sign_marketplace(raw: bytes, bindings: list[dict], sequence: int, signer: Ed25519PrivateKey, *, views: list[dict], trust_policy=None, view_root=None, artifact_roots=None, verified_contexts=None) -> bytes:
    payload = signing_payload(raw, bindings, sequence, views, trust_policy, view_root, artifact_roots, verified_contexts)
    expected = trust_policy["keys"][payload["publisherKeyId"]]["publicKey"]
    if not isinstance(signer, Ed25519PrivateKey) or signer.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex() != expected:
        raise ValueError("marketplaceSignerMismatch")
    return canonical(sign_envelope(payload, signer))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("marketplace", "bindings", "views", "policy", "artifact-roots", "verified-contexts", "view-root", "output"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--sequence", required=True, type=int)
    args = parser.parse_args()
    try:
        raw = args.marketplace.read_bytes()
        bindings, views, policy = [read_json(path.read_bytes()) for path in (args.bindings, args.views, args.policy)]
        roots = {key: Path(value) for key, value in read_json(args.artifact_roots.read_bytes()).items()}
        contexts = read_json(args.verified_contexts.read_bytes())
        # Fail closed on all public inputs before reading the anonymous signing pipe.
        payload = signing_payload(raw, bindings, args.sequence, views, policy, args.view_root, roots, contexts)
        key = sys.stdin.buffer.read(33)
        if len(key) != 32:
            raise ValueError("invalidSigningKey")
        result = sign_marketplace(raw, bindings, args.sequence, Ed25519PrivateKey.from_private_bytes(key), views=views, trust_policy=policy, view_root=args.view_root, artifact_roots=roots, verified_contexts=contexts)
        if read_json(result)["marketplaceSha256"] != payload["marketplaceSha256"]:
            raise ValueError("candidateChangedDuringSigning")
        with args.output.open("xb") as stream:
            stream.write(result)
    except Exception:
        parser.exit(1, "marketplaceSigningFailed\n")


if __name__ == "__main__":
    main()
