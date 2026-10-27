"""Prepare a reviewed signed skill draft; this module never publishes assets."""
import re
import time
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

try:
    from .sign_envelope import sign_envelope
    from .verify_artifact import digest, read_json
    from .verify_skill_artifact import verify
except ImportError:
    from sign_envelope import sign_envelope
    from verify_artifact import digest, read_json
    from verify_skill_artifact import verify

REPOSITORY = "github:XIAOYUGAN666/opendesign-plugins"


def sign_draft(archive, candidate, context, host_policy, key_id, signer):
    before = candidate.read_bytes()
    payload = read_json(before)
    release = payload["release"]
    package, version = release["packageId"], release["pluginVersion"]
    commit = context.get("sourceCommit", "")
    tag = f"plugin-{package}-v{version}"
    url = f"https://github.com/XIAOYUGAN666/opendesign-plugins/releases/download/{tag}/{archive.name}"
    if (context.get("draft") is not True or not re.fullmatch(r"[a-f0-9]{40}", commit)
            or context.get("targetCommit") != commit or context.get("packageId") != package
            or context.get("version") != version or context.get("tag") != tag
            or context.get("assetUrl") != url or context.get("archiveDigest") != payload["archive"]["sha256"]
            or not re.fullmatch(r"[A-Za-z0-9._-]+\.zip", archive.name)):
        raise ValueError("invalidDraftContext")
    owner = host_policy.get("publishers", {}).get(key_id)
    public = signer.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
    if (not owner or owner.get("publicKey") != public or owner.get("scope", {}).get("repositoryIdentity") != REPOSITORY
            or package not in owner["scope"].get("releasePackages", [])):
        raise ValueError("invalidHostPolicy")
    verify(archive, payload, unsigned=True)
    if candidate.read_bytes() != before or digest(archive.read_bytes()) != context["archiveDigest"]:
        raise ValueError("candidateChangedDuringSigning")
    payload["publisherKeyId"] = key_id
    payload["archive"]["url"] = url
    now = int(time.time())
    payload["notBefore"], payload["expiresAt"] = now - 60, now + 365 * 24 * 3600
    return sign_envelope(payload, signer)
