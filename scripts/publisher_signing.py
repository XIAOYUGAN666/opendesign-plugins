"""Controlled candidate signing; Host public policy is exported, never replaced here."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

try:
    from .sign_envelope import sign_envelope
    from .verify_artifact import canonical, read_json, verify_artifact
except ImportError:
    from sign_envelope import sign_envelope
    from verify_artifact import canonical, read_json, verify_artifact


# Target labels do not add aliases to Host trust or mutate existing signed receipts.
TARGETS = {
    "dlubal-rfem": ("publisher-v1", "rfem-publisher-v1", "RFEM_PUBLISHER_V1_KEY_B64"),
    "rhino": ("rhino-publisher-v1", "rhino-publisher-v1", "RHINO_PUBLISHER_V1_KEY_B64"),
}
REPOSITORY = "github:XIAOYUGAN666/opendesign-plugins"
SAFE_ERRORS = {"missingPublisherSecret", "invalidPublisherSecret", "publisherKeyMismatch", "publisherKeysNotIndependent", "invalidHostPolicyExport", "invalidDraftContext", "candidateChangedDuringSigning"}


def take_secrets():
    # Remove BOTH step-local secrets before any validation, failure or Provider child.
    return {package: os.environ.pop(fields[2], "") for package, fields in TARGETS.items()}


def load_signers(schema_bundle, encoded):
    path = schema_bundle / "host-publisher-policy.json"
    raw = path.read_bytes()
    inventory = read_json((schema_bundle / "owner-contracts.json").read_bytes())
    owner = inventory["contracts"][path.name]
    if owner != {"owner": "host", "source": "apps/desktop/src-tauri/resources/plugins/publisher-trust.v1.json", "version": "1", "sha256": hashlib.sha256(raw).hexdigest()}:
        raise ValueError("invalidHostPolicyExport")
    policy = read_json(raw)
    if policy.get("schemaVersion") != 1:
        raise ValueError("invalidHostPolicyExport")
    signers, reports = {}, []
    for package, (current_id, target_id, _) in TARGETS.items():
        host_id = target_id if target_id in policy["publishers"] else current_id
        publisher = policy["publishers"][host_id]
        if current_id != target_id and current_id in policy["publishers"] and target_id in policy["publishers"] and policy["publishers"][current_id] != publisher:
            raise ValueError("invalidHostPolicyExport")
        scope = publisher["scope"]
        if scope["repositoryIdentity"] != REPOSITORY or set(scope["releasePackages"]) != {package}:
            raise ValueError("invalidHostPolicyExport")
        if not encoded.get(package):
            raise ValueError("missingPublisherSecret")
        try:
            raw_key = base64.b64decode(encoded[package], validate=True)
            if len(raw_key) != 32:
                raise ValueError()
            signer = Ed25519PrivateKey.from_private_bytes(raw_key)
        except Exception:
            raise ValueError("invalidPublisherSecret") from None
        public = signer.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        if public.hex() != publisher["publicKey"]:
            raise ValueError("publisherKeyMismatch")
        signers[package] = signer
        reports.append({"packageId": package, "hostPolicyKeyId": host_id, "candidateKeyId": target_id, "publicKeySha256": hashlib.sha256(public).hexdigest(), "hostIdActivationRequired": host_id != target_id})
    if reports[0]["publicKeySha256"] == reports[1]["publicKeySha256"]:
        raise ValueError("publisherKeysNotIndependent")
    return signers, reports


def sign_draft_candidate(archive, candidate, schema_bundle, context, signer):
    package = context.get("packageId")
    if package not in TARGETS or context.get("draft") is not True:
        raise ValueError("invalidDraftContext")
    commit = context.get("sourceCommit", "")
    if not re.fullmatch(r"[a-f0-9]{40}", commit) or context.get("targetCommit") != commit:
        raise ValueError("invalidDraftContext")
    before = candidate.read_bytes()
    payload = read_json(before)
    release = payload["release"]
    version = release["pluginVersion"]
    if release["packageId"] != package or context.get("version") != version or not re.fullmatch(r"[A-Za-z0-9._-]+\.zip", archive.name):
        raise ValueError("invalidDraftContext")
    url = f"https://github.com/XIAOYUGAN666/opendesign-plugins/releases/download/plugin-{package}-v{version}/{archive.name}"
    if context.get("assetUrl") != url or context.get("tag") != f"plugin-{package}-v{version}" or context.get("archiveDigest") != payload["archive"]["sha256"]:
        raise ValueError("invalidDraftContext")
    # Only the existing verifier executes metadata discovery, with secrets removed.
    verified = verify_artifact(archive, candidate, schema_bundle, unsigned_candidate=True)
    if candidate.read_bytes() != before or "sha256:" + hashlib.sha256(archive.read_bytes()).hexdigest() != context["archiveDigest"]:
        raise ValueError("candidateChangedDuringSigning")
    payload["publisherKeyId"] = TARGETS[package][1]
    payload["archive"]["url"] = url
    now = int(time.time())
    payload["notBefore"], payload["expiresAt"] = now - 60, now + 365 * 24 * 3600
    signed = sign_envelope(payload, signer)
    signer.public_key().verify(bytes.fromhex(signed["signature"]), canonical({"signatureSchemaVersion": 1, **payload}))
    return signed, {**verified, "verification": "signed-draft-candidate", "candidateKeyId": TARGETS[package][1], "stablePublication": False}


def main():
    encoded = take_secrets()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schemas", type=Path, required=True)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--context", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        signers, reports = load_signers(args.schemas, encoded)
        encoded.clear()
        result = {"keyVerification": reports, "hostTrustModified": False, "stablePublication": False}
        if any((args.archive, args.candidate, args.context, args.output)):
            if not all((args.archive, args.candidate, args.context, args.output)):
                raise ValueError("invalidDraftContext")
            context = read_json(args.context.read_bytes())
            if context.get("packageId") not in signers:
                raise ValueError("invalidDraftContext")
            signed, verification = sign_draft_candidate(args.archive, args.candidate, args.schemas, context, signers[context["packageId"]])
            with args.output.open("xb") as stream:
                stream.write(canonical(signed))
            result["candidate"] = verification
        print(json.dumps(result, sort_keys=True))
    except Exception as error:
        category = str(error) if type(error) is ValueError and str(error) in SAFE_ERRORS else "publisherSigningFailed"
        parser.exit(1, category + "\n")
    finally:
        encoded.clear()


if __name__ == "__main__":
    main()
