"""Generate the official discovery file from approved immutable views only."""
import argparse
import os
from pathlib import Path
import tempfile

try:
    from .verify_artifact import canonical, read_json
except ImportError:
    from verify_artifact import canonical, read_json


def verified_views(bindings, view_root, trust_policy, artifact_roots, verified_contexts):
    # Lazy imports keep the empty discovery CLI standard-library only.
    try:
        from .generate_stable_view import approved_portable_files, authorize, inventory_for, snapshot_files
    except ImportError:
        from generate_stable_view import approved_portable_files, authorize, inventory_for, snapshot_files
    views, identities = [], []
    seen = set()
    archives = set()
    for receipt in bindings:
        release = authorize(receipt, trust_policy, allow_revoked=True)
        archive = receipt["archive"]["sha256"]
        if archive in archives:
            raise ValueError("duplicateOrConflictingArchiveStatement")
        archives.add(archive)
        # Authentic tombstones remain signed bindings, never discovery or assets.
        if receipt["revoked"]:
            continue
        package = release["packageId"]
        if package in seen:
            raise ValueError("duplicateStableIdentity")
        seen.add(package)
        selected = approved_portable_files((artifact_roots or {})[package], receipt, trust_policy, (verified_contexts or {})[package])
        if snapshot_files(view_root / "published" / package) != selected:
            raise ValueError("stableViewArtifactMismatch")
        identities.append(read_json(selected["plugin.json"]))
        views.append(inventory_for(package, selected))
    return views, identities


def marketplace_document(identities):
    return {"name": "team-stable", "interface": {"displayName": "Official stable plugins"}, "plugins": [{"name": identity["name"], "source": {"source": "local", "path": "./published/" + identity["name"]}, "policy": {"installation": "AVAILABLE", "authentication": "ON_USE"}, "category": "Engineering Design"} for identity in sorted(identities, key=lambda item: item["name"])]}


def generate_marketplace(bindings: list[dict], output: Path, *, view_root: Path, trust_policy=None, artifact_roots=None, verified_contexts=None) -> Path:
    _, identities = verified_views(bindings, view_root, trust_policy, artifact_roots, verified_contexts) if bindings else ([], [])
    raw = canonical(marketplace_document(identities))
    # Complete validation precedes atomic replacement; failed candidates preserve bytes.
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=output.parent, prefix=".marketplace-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(raw)
        os.replace(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bindings", type=Path)
    parser.add_argument("--view-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--artifact-roots", type=Path, help="Caller map of package ID to verified extracted root")
    parser.add_argument("--verified-contexts", type=Path)
    args = parser.parse_args()
    try:
        roots = {key: Path(value) for key, value in read_json(args.artifact_roots.read_bytes()).items()} if args.artifact_roots else None
        generate_marketplace(read_json(args.bindings.read_bytes()) if args.bindings else [], args.output, view_root=args.view_root, trust_policy=read_json(args.policy.read_bytes()) if args.policy else None, artifact_roots=roots, verified_contexts=read_json(args.verified_contexts.read_bytes()) if args.verified_contexts else None)
    except Exception:
        parser.exit(1, "marketplaceGenerationFailed\n")


if __name__ == "__main__":
    main()
