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
import subprocess

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

try:
    from .sign_envelope import sign_envelope
    from .verify_artifact import canonical, read_json, verify_artifact
except ImportError:
    from sign_envelope import sign_envelope
    from verify_artifact import canonical, read_json, verify_artifact


# Only current publisher identities may sign; historical IDs are not fallbacks.
TARGETS = {
    "dlubal-rfem": ("rfem-publisher-v1", "RFEM_PUBLISHER_V1_KEY_B64"),
    "rhino": ("rhino-publisher-v1", "RHINO_PUBLISHER_V1_KEY_B64"),
}
REPOSITORY = "github:XIAOYUGAN666/opendesign-plugins"
SAFE_ERRORS = {"missingPublisherSecret", "invalidPublisherSecret", "publisherKeyMismatch", "publisherKeysNotIndependent", "invalidHostPolicyExport", "invalidDraftContext", "invalidPreviousMarket", "candidateChangedDuringSigning", "publisherSecretInRepository"}


def is_connector_release(release):
    # Signed releases created before the connector naming change keep their
    # immutable providerBuildId field; newly built releases use connectorBuildId.
    return "connectorBuildId" in release or "providerBuildId" in release


def take_secrets():
    # Remove BOTH step-local secrets before any validation, failure or Connector child.
    return {package: os.environ.pop(fields[1], "") for package, fields in TARGETS.items()}


def reject_tracked_private_material(encoded, signers):
    root = Path(__file__).resolve().parents[1]
    names = subprocess.check_output(["git", "ls-files", "-z"], cwd=root).decode().split("\0")
    needles = []
    for package, signer in signers.items():
        raw = signer.private_bytes_raw()
        needles.extend((encoded[package].encode(), raw, raw.hex().encode(), raw.hex().upper().encode()))
    for name in names:
        if not name:
            continue
        path = root / name
        if path.is_symlink() or path.is_junction() or not path.resolve().is_relative_to(root):
            raise ValueError("publisherSecretInRepository")
        if not path.exists():
            continue
        data = path.read_bytes()
        if any(needle in data for needle in needles):
            raise ValueError("publisherSecretInRepository")


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
    for package, (target_id, _) in TARGETS.items():
        publisher = policy["publishers"].get(target_id)
        if publisher is None:
            raise ValueError("invalidHostPolicyExport")
        scope = publisher["scope"]
        if scope["repositoryIdentity"] != REPOSITORY or package not in scope["releasePackages"]:
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
        reports.append({"packageId": package, "hostPolicyKeyId": target_id, "candidateKeyId": target_id, "publicKeySha256": hashlib.sha256(public).hexdigest()})
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
    payload["publisherKeyId"] = TARGETS[package][0]
    payload["archive"]["url"] = url
    now = int(time.time())
    payload["notBefore"], payload["expiresAt"] = now - 60, now + 365 * 24 * 3600
    signed = sign_envelope(payload, signer)
    signer.public_key().verify(bytes.fromhex(signed["signature"]), canonical({"signatureSchemaVersion": 1, **payload}))
    return signed, {**verified, "verification": "signed-draft-candidate", "candidateKeyId": TARGETS[package][0], "assetUrlVerification": "pending-anonymous-publication-check", "stablePublication": False}


def sign_skill_draft_candidate(archive, candidate, context, host_policy, signers):
    try:
        from .sign_skill_artifact import sign_draft
        from .verify_skill_artifact import verify
    except ImportError:
        from sign_skill_artifact import sign_draft
        from verify_skill_artifact import verify
    package = context.get("packageId")
    owners = [(key_id, signers[owner]) for owner, (key_id, _) in TARGETS.items()
              if owner in signers and package in host_policy.get("publishers", {}).get(key_id, {}).get("scope", {}).get("releasePackages", [])]
    if len(owners) != 1:
        raise ValueError("invalidHostPolicyExport")
    key_id, signer = owners[0]
    signed = sign_draft(archive, candidate, context, host_policy, key_id, signer)
    public = signer.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    verified = verify(archive, signed, trusted_keys={key_id: public})
    return signed, {**verified, "verification": "signed-draft-candidate", "candidateKeyId": key_id,
                    "assetUrlVerification": "pending-anonymous-publication-check", "stablePublication": False}


def verify_previous_market(repository, schema_bundle, host_policy, expected_digest):
    """Authenticate the exact previous market and its complete immutable views."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    try:
        from .generate_stable_view import inventory_for, snapshot_files, authorize
        from .generate_marketplace import marketplace_document
        from .verify_artifact import digest, validate
    except ImportError:
        from generate_stable_view import inventory_for, snapshot_files, authorize
        from generate_marketplace import marketplace_document
        from verify_artifact import digest, validate
    try:
        raw = (repository / ".agents/plugins/marketplace.snapshot.json").read_bytes()
        if digest(raw) != expected_digest:
            raise ValueError()
        snapshot = read_json(raw)
        validate(snapshot, read_json((schema_bundle / "host-contract.v3.schema.json").read_bytes()), "SignedMarketplaceV1")
        publisher = host_policy["publishers"][snapshot["publisherKeyId"]]
        scope = publisher["scope"]
        now = int(time.time())
        if (snapshot["repositoryIdentity"] != REPOSITORY or scope["repositoryIdentity"] != REPOSITORY
                or not 0 < snapshot["sequence"] < 2**64 - 1
                or not snapshot["notBefore"] <= now < snapshot["expiresAt"]):
            raise ValueError()
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(publisher["publicKey"])).verify(
            bytes.fromhex(snapshot["signature"]), canonical({"signatureSchemaVersion": 1, **{k:v for k,v in snapshot.items() if k != "signature"}}))
        market = (repository / ".agents/plugins/marketplace.json").read_bytes()
        if digest(market) != snapshot["marketplaceSha256"]:
            raise ValueError()
        previous, identities = {}, []
        policy = {"repositoryIdentity": REPOSITORY, "keys": {}, "approvedArchives": {}, "revokedArchives": []}
        for receipt in snapshot["releases"] + snapshot.get("skillReleases", []):
            package = receipt["release"]["packageId"]
            owner = host_policy["publishers"][receipt["publisherKeyId"]]
            # This update operation requires a complete active replacement set;
            # revocation/tombstone changes require their own explicitly reviewed operation.
            if (receipt["revoked"] or package in previous or package not in scope["marketplacePackages"]
                    or owner["scope"]["repositoryIdentity"] != REPOSITORY
                    or package not in owner["scope"]["releasePackages"]):
                raise ValueError()
            policy["keys"][receipt["publisherKeyId"]] = {"publicKey":owner["publicKey"], "packages":owner["scope"]["releasePackages"], "notBefore":1, "expiresAt":2**64-1, "revoked":False}
            policy["approvedArchives"][receipt["archive"]["sha256"]] = receipt["archive"]["url"]
            authorize(receipt, policy)
            previous[package] = receipt["release"]
        if not previous or {v["packageId"] for v in snapshot["views"]} != set(previous) or len(snapshot["views"]) != len(previous):
            raise ValueError()
        for view in snapshot["views"]:
            package = view["packageId"]
            files = snapshot_files(repository / "published" / package)
            if inventory_for(package, files) != view:
                raise ValueError()
            identity = read_json(files["plugin.json"])
            if identity["name"] != package or identity["version"] != previous[package]["pluginVersion"]:
                raise ValueError()
            identities.append(identity)
        # Discovery order is deterministic by package ID, independent of view ordering.
        if read_json(market) != marketplace_document(sorted(identities, key=lambda i: i["name"])):
            raise ValueError()
        return snapshot["sequence"], previous
    except Exception:
        raise ValueError("invalidPreviousMarket") from None


def require_market_progression(previous, bindings, *, previous_receipts=None, retired_package=None):
    package_ids = {r["release"]["packageId"] for r in bindings}
    if retired_package is not None:
        if (previous_receipts is None or retired_package not in previous
                or package_ids != set(previous) - {retired_package}
                or len(bindings) != len(package_ids)
                or any(previous_receipts.get(r["release"]["packageId"]) != r for r in bindings)):
            raise ValueError("invalidPreviousMarket")
        return
    if not set(previous) <= package_ids or len(bindings) != len(package_ids):
        raise ValueError("invalidPreviousMarket")
    for receipt in bindings:
        release = receipt["release"]
        if release["packageId"] not in previous:
            continue
        old = previous[release["packageId"]]
        if previous_receipts is not None and previous_receipts.get(release["packageId"]) == receipt:
            continue
        versions = (old["pluginVersion"], release["pluginVersion"])
        if (not all(re.fullmatch(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", v) for v in versions)
                or tuple(map(int, versions[1].split('.'))) <= tuple(map(int, versions[0].split('.')))
                or release["releaseSequence"] <= old["releaseSequence"]):
            raise ValueError("invalidPreviousMarket")


def validate_market_entry_context(receipt, context, archive_name, source_commit, previous_receipts):
    release = receipt["release"]
    package, version = release["packageId"], release["pluginVersion"]
    tag = f"plugin-{package}-v{version}"
    url = f"https://github.com/XIAOYUGAN666/opendesign-plugins/releases/download/{tag}/{archive_name}"
    if (not re.fullmatch(r"[A-Za-z0-9._-]+\.zip", archive_name)
            or context.get("tag") != tag or context.get("packageId") != package
            or context.get("version") != version or context.get("assetUrl") != url
            or receipt["archive"]["url"] != url
            or context.get("archiveDigest") != receipt["archive"]["sha256"]):
        raise ValueError("invalidDraftContext")
    if previous_receipts.get(package) == receipt:
        if context.get("published") is not True:
            raise ValueError("invalidDraftContext")
    elif (context.get("draft") is not True or context.get("sourceCommit") != source_commit
          or context.get("targetCommit") != source_commit):
        raise ValueError("invalidDraftContext")


def sign_market_candidate(entries, schema_bundle, host_policy, signer, market_key_id, source_commit, output, *, previous_market_digest=None, retired_package=None):
    """Prepare an isolated initial or authenticated subsequent market review.

    This is release tooling, not Host authority. Full artifact verification and
    existing view/market generators own their respective checks.
    """
    import tempfile
    try:
        from .generate_stable_view import generate_stable_view, regular_directory
        from .generate_marketplace import generate_marketplace, verified_views
        from .sign_marketplace import sign_marketplace
        from .verify_artifact import extract, digest
    except ImportError:
        from generate_stable_view import generate_stable_view, regular_directory
        from generate_marketplace import generate_marketplace, verified_views
        from sign_marketplace import sign_marketplace
        from verify_artifact import extract, digest

    output = regular_directory(output.absolute())
    repository = Path(__file__).resolve().parents[1]
    if output.exists() or (output.is_relative_to(repository) and not output.is_relative_to(repository / "dist")):
        raise ValueError("invalidDraftContext")
    if retired_package is not None and (retired_package != "rfem-timber-engineering" or previous_market_digest is None):
        raise ValueError("invalidDraftContext")
    formal = read_json((repository / ".agents/plugins/marketplace.json").read_bytes())
    previous_sequence, previous = 0, None
    if previous_market_digest is not None:
        previous_sequence, previous = verify_previous_market(repository, schema_bundle, host_policy, previous_market_digest)
    elif formal.get("plugins") or (repository / ".agents/plugins/marketplace.snapshot.json").exists():
        raise ValueError("invalidDraftContext")
    if not entries or len(entries) > 64 or not re.fullmatch(r"[a-f0-9]{40}", source_commit):
        raise ValueError("invalidDraftContext")
    previous_receipts = {}
    if previous is not None:
        previous_raw = (repository / ".agents/plugins/marketplace.snapshot.json").read_bytes()
        if digest(previous_raw) != previous_market_digest:
            raise ValueError("invalidPreviousMarket")
        previous_snapshot = read_json(previous_raw)
        previous_receipts = {receipt["release"]["packageId"]: receipt for receipt in previous_snapshot["releases"] + previous_snapshot.get("skillReleases", [])}
    publishers = host_policy["publishers"]
    approved, bindings, keys, packages = {}, [], {}, set()
    for item in entries:
        receipt_path, archive = Path(item["receipt"]), Path(item["archive"])
        receipt = read_json(receipt_path.read_bytes())
        release, context = receipt["release"], item["context"]
        package = release["packageId"]
        owner = publishers.get(receipt["publisherKeyId"])
        if (not owner or owner["scope"]["repositoryIdentity"] != REPOSITORY
                or package not in owner["scope"]["releasePackages"]
                or package in packages or not re.fullmatch(r"[a-z0-9]+(?:[.-][a-z0-9]+)*", package)):
            raise ValueError("invalidHostPolicyExport")
        validate_market_entry_context(receipt, context, archive.name, source_commit, previous_receipts)
        packages.add(package)
        bindings.append(receipt)
        approved[receipt["archive"]["sha256"]] = receipt["archive"]["url"]
    if previous is not None:
        require_market_progression(previous, bindings, previous_receipts=previous_receipts, retired_package=retired_package)
    market_owner = publishers.get(market_key_id)
    if (not market_owner or market_owner["scope"]["repositoryIdentity"] != REPOSITORY
            or not packages <= set(market_owner["scope"]["marketplacePackages"])
            or signer.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex() != market_owner["publicKey"]):
        raise ValueError("publisherKeyMismatch")
    for key_id, publisher in publishers.items():
        scope = publisher["scope"]
        keys[key_id] = {"publicKey": publisher["publicKey"], "packages": sorted(set(scope["releasePackages"]) | set(scope["marketplacePackages"])),
                        "notBefore": 1, "expiresAt": max(r["expiresAt"] for r in bindings), "revoked": False}
    policy = {"repositoryIdentity": REPOSITORY, "keys": keys, "approvedArchives": approved,
              "marketplaceKeyId": market_key_id, "previousSequence": previous_sequence, "revokedArchives": []}
    contexts, roots = {}, {}
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="market-review-", dir=output.parent) as temporary:
        temporary = Path(temporary)
        staged = temporary / "review"
        for item, receipt in zip(entries, bindings):
            package = receipt["release"]["packageId"]
            if is_connector_release(receipt["release"]):
                contexts[package] = verify_artifact(Path(item["archive"]), Path(item["receipt"]), schema_bundle,
                    trusted_keys={receipt["publisherKeyId"]: bytes.fromhex(publishers[receipt["publisherKeyId"]]["publicKey"])})
            else:
                try:
                    from .verify_skill_artifact import verify as verify_skill
                except ImportError:
                    from verify_skill_artifact import verify as verify_skill
                contexts[package] = verify_skill(Path(item["archive"]), receipt,
                    trusted_keys={receipt["publisherKeyId"]: bytes.fromhex(publishers[receipt["publisherKeyId"]]["publicKey"])})
            root = temporary / "artifacts" / package
            root.mkdir(parents=True)
            extract(Path(item["archive"]), root)
            if digest(Path(item["archive"]).read_bytes()) != receipt["archive"]["sha256"]:
                raise ValueError("candidateChangedDuringSigning")
            roots[package] = root
            generate_stable_view(root, receipt, staged, trust_policy=policy, verified_context=contexts[package])
        market = staged / ".agents/plugins/marketplace.json"
        generate_marketplace(bindings, market, view_root=staged, trust_policy=policy, artifact_roots=roots, verified_contexts=contexts)
        views, _ = verified_views(bindings, staged, policy, roots, contexts)
        # Metadata probes run child processes: retain the exact reviewed baseline
        # through the signing boundary, not only before those probes started.
        if previous_market_digest is not None:
            verify_previous_market(repository, schema_bundle, host_policy, previous_market_digest)
        sequence = previous_sequence + 1
        snapshot = sign_marketplace(market.read_bytes(), bindings, sequence, signer, views=views,
            trust_policy=policy, view_root=staged, artifact_roots=roots, verified_contexts=contexts)
        (market.parent / "marketplace.snapshot.json").write_bytes(snapshot)
        report = {"sequence": sequence, "previousSnapshotSha256": previous_market_digest, "sourceCommit": source_commit, "marketplaceSha256": digest(market.read_bytes()),
                  "snapshotSha256": digest(snapshot), "views": views, "stablePublication": False,
                  "assetUrlVerification": "pending-anonymous-publication-check", "finalApprovalRequired": True}
        (staged / "review.json").write_bytes(canonical(report))
        if previous_market_digest is not None:
            verify_previous_market(repository, schema_bundle, host_policy, previous_market_digest)
        staged.rename(output)
    return report


def main():
    encoded = take_secrets()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schemas", type=Path, required=True)
    parser.add_argument("--market-inputs", type=Path)
    parser.add_argument("--previous-market-digest")
    parser.add_argument("--retire-package", choices=["rfem-timber-engineering"])
    parser.add_argument("--source-commit")
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--context", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        signers, reports = load_signers(args.schemas, encoded)
        reject_tracked_private_material(encoded, signers)
        encoded.clear()
        if (args.previous_market_digest or args.retire_package) and not args.market_inputs:
            raise ValueError("invalidDraftContext")
        result = {"keyVerification": reports, "hostTrustModified": False, "trackedPrivateMaterial": False, "stablePublication": False}
        if args.market_inputs:
            if not args.output or not args.source_commit or any((args.archive, args.candidate, args.context)):
                raise ValueError("invalidDraftContext")
            entries = read_json(args.market_inputs.read_bytes())
            result["marketCandidate"] = sign_market_candidate(entries, args.schemas,
                read_json((args.schemas / "host-publisher-policy.json").read_bytes()),
                signers["dlubal-rfem"], TARGETS["dlubal-rfem"][0], args.source_commit, args.output, previous_market_digest=args.previous_market_digest, retired_package=args.retire_package)
        elif any((args.archive, args.candidate, args.context, args.output)):
            if not all((args.archive, args.candidate, args.context, args.output)):
                raise ValueError("invalidDraftContext")
            context = read_json(args.context.read_bytes())
            payload = read_json(args.candidate.read_bytes())
            if is_connector_release(payload.get("release", {})):
                if context.get("packageId") not in signers:
                    raise ValueError("invalidDraftContext")
                signed, verification = sign_draft_candidate(args.archive, args.candidate, args.schemas, context, signers[context["packageId"]])
            else:
                signed, verification = sign_skill_draft_candidate(args.archive, args.candidate, context,
                    read_json((args.schemas / "host-publisher-policy.json").read_bytes()), signers)
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
