"""Project approved extracted artifact bytes into an immutable portable view.

The caller owns publication approval and provides an in-memory trust policy.
This tool neither downloads assets nor accepts an input's claim to be approved.
"""
import argparse
from pathlib import Path
import re
import tempfile
import time
from urllib.parse import unquote, urlsplit

try:
    from .verify_artifact import MAX_ARCHIVE, MAX_FILE, NAMESPACE, canonical, digest, read_json, release_identity, safe_path, validate
except ImportError:
    from verify_artifact import MAX_ARCHIVE, MAX_FILE, NAMESPACE, canonical, digest, read_json, release_identity, safe_path, validate

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "schemas"
REPOSITORY = "github:XIAOYUGAN666/opendesign-plugins"
RELEASE_ORIGIN = "https://github.com/XIAOYUGAN666/opendesign-plugins"
STABLE_VERSION = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")


def contract(value, definition):
    validate(value, read_json((SCHEMAS / "host-contract.v3.schema.json").read_bytes()), definition)


def authorize(receipt, policy, *, allow_revoked=False):
    """Validate release signature and caller authorization before using its bytes."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    if not policy or policy.get("repositoryIdentity") != REPOSITORY:
        raise ValueError("missingPublicationAuthorization")
    contract(receipt, "SignedReleaseV1")
    release = receipt["release"]
    package = release["packageId"]
    if not re.fullmatch(r"[a-z0-9]+(?:[.-][a-z0-9]+)*", package) or not STABLE_VERSION.fullmatch(release["pluginVersion"]):
        raise ValueError("invalidStableIdentity")
    key = policy.get("keys", {}).get(receipt["publisherKeyId"])
    now = int(time.time())
    if not key or package not in key.get("packages", []) or key.get("revoked") is not False:
        raise ValueError("signerScopeDenied")
    if any(type(x) is not int or not 0 <= x < 2**64 for x in (key.get("notBefore"), key.get("expiresAt"))):
        raise ValueError("invalidAuthorizationTime")
    if not 0 < key["notBefore"] <= receipt["notBefore"] <= now < receipt["expiresAt"] <= key["expiresAt"] or (receipt["revoked"] and not allow_revoked):
        raise ValueError("expiredOrRevokedAuthorization")
    if not re.fullmatch(r"[a-f0-9]{128}", receipt["signature"]):
        raise ValueError("invalidReleaseSignature")
    Ed25519PublicKey.from_public_bytes(bytes.fromhex(key["publicKey"])).verify(bytes.fromhex(receipt["signature"]), canonical({"signatureSchemaVersion": 1, **{k: v for k, v in receipt.items() if k != "signature"}}))
    archive = receipt["archive"]
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", archive["sha256"]) or not 0 < archive["byteLength"] <= MAX_ARCHIVE or release["releaseSequence"] <= 0:
        raise ValueError("invalidReleaseBinding")
    if (not receipt["revoked"] and archive["sha256"] in policy.get("revokedArchives", [])) or policy.get("approvedArchives", {}).get(archive["sha256"]) != archive["url"]:
        raise ValueError("releaseNotApproved")
    url = urlsplit(archive["url"])
    tag = f"plugin-{package}-v{release['pluginVersion']}"
    prefix = f"/XIAOYUGAN666/opendesign-plugins/releases/download/{tag}/"
    if url.scheme != "https" or url.netloc != "github.com" or url.query or url.fragment or not url.path.startswith(prefix) or len(safe_path(unquote(url.path[len(prefix):]))) != 1:
        raise ValueError("releaseSourceDenied")
    return release


def regular_directory(root):
    for path in (root, *root.parents):
        if path.is_symlink() or path.is_junction():
            raise ValueError("reparseArtifact")
    return root.resolve()


def snapshot_files(root):
    root = regular_directory(root)
    result = {}
    folded = set()
    pending = []
    total = 0
    for path in root.rglob("*"):
        if path.is_symlink() or path.is_junction():
            raise ValueError("reparseArtifact")
        if path.is_file():
            name = path.relative_to(root).as_posix()
            safe_path(name)
            if name.casefold() in folded:
                raise ValueError("duplicateArtifactPath")
            folded.add(name.casefold())
            size = path.stat().st_size
            total += size
            if not 0 <= size <= MAX_FILE or total > MAX_ARCHIVE:
                raise ValueError("artifactSizeLimit")
            pending.append((name, path, size))
    # Preflight the entire metadata inventory before allocating any file bytes.
    for name, path, size in pending:
        with path.open("rb") as stream:
            raw = stream.read(size + 1)
        if len(raw) != size:
            raise ValueError("artifactChangedDuringRead")
        result[name] = raw
    return result


def portable_files(files, identity):
    configuration = identity["extensions"][NAMESPACE]["configurationSchemaPath"]
    selected = {name: raw for name, raw in files.items() if name in {"plugin.json", "README.md", configuration, "tool-preview.json", "mcp.json", "hooks.json"} or ("/" not in name and name.endswith(".md")) or name.split("/")[0] in {"skills", "assets", "agents", "commands", "hooks"}}
    if not {"plugin.json", "README.md", configuration, "tool-preview.json"} <= selected.keys() or not any(name.startswith("skills/") and name.endswith("/SKILL.md") for name in selected):
        raise ValueError("incompletePortableView")
    if "mcp.json" in selected:
        # No standard managed endpoint has been approved for the first plugins.
        raise ValueError("unapprovedStandardMcp")
    native = identity["extensions"][NAMESPACE]
    installed_paths = [native["entrypoint"]]
    if native.get("native"):
        installed_paths.append(native["native"]["relativePath"])
    for name in installed_paths:
        safe_path(name)
        if name not in files or name in selected:
            raise ValueError("nativeEntrypointOutsideInstallAsset")
    for name, raw in selected.items():
        if name.lower().endswith((".exe", ".dll", ".pyd", ".so", ".dylib", ".zip", ".yak", ".key", ".pem")) or (name.endswith((".md", ".json")) and b"\0" in raw):
            raise ValueError("nativeAssetInPortableView")
        if name.endswith(".md"):
            for target in re.findall(r"!?\[[^\]]*\]\(([^\s)]+)(?:\s+[^)]*)?\)", raw.decode("utf-8")):
                parsed = urlsplit(target.strip("<>"))
                if parsed.scheme or parsed.netloc or not parsed.path:
                    continue
                path = Path(name).parent / unquote(parsed.path)
                parts = []
                for part in path.parts:
                    if part == "..":
                        if not parts:
                            raise ValueError("portableReferenceEscapes")
                        parts.pop()
                    elif part != ".":
                        parts.append(part)
                resolved = "/".join(parts)
                safe_path(resolved)
                if resolved not in selected:
                    raise ValueError("brokenPortableReference")
    return selected


def inventory_for(package, files):
    result = {"packageId": package, "sourcePath": f"./published/{package}", "files": [{"path": name, "byteLength": len(raw), "sha256": digest(raw)} for name, raw in sorted(files.items())]}
    contract(result, "StableViewInventoryV1")
    return result


def approved_portable_files(artifact_root, receipt, trust_policy, verified_context):
    release = authorize(receipt, trust_policy)
    expected_context = {"archiveDigest": receipt["archive"]["sha256"], "filesDigest": release["filesDigest"], "toolCatalogDigest": release["toolCatalogDigest"], "verification": "signed-receipt"}
    if verified_context != expected_context:
        raise ValueError("verifiedArtifactContextRequired")
    artifact_root = regular_directory(artifact_root.absolute())
    if artifact_root.is_relative_to((ROOT / "plugins").resolve()):
        raise ValueError("developmentRootForbidden")
    files = snapshot_files(artifact_root)
    if read_json(files["release.json"]) != release or digest(files["files.json"]) != release["filesDigest"]:
        raise ValueError("releaseInventoryMismatch")
    declared = read_json(files["files.json"])
    if set(declared) != {"schemaVersion", "files"} or declared["schemaVersion"] != 1:
        raise ValueError("invalidArtifactInventory")
    expected = {"release.json", "files.json"}
    for item in declared["files"]:
        name = item["path"]
        safe_path(name)
        if set(item) != {"path", "byteLength", "sha256"} or name in expected or type(item["byteLength"]) is not int or len(files[name]) != item["byteLength"] or digest(files[name]) != item["sha256"]:
            raise ValueError("artifactFileMismatch")
        expected.add(name)
    if set(files) != expected:
        raise ValueError("undeclaredArtifactFile")
    identity = read_json(files["plugin.json"])
    validate(identity, read_json((SCHEMAS / "agent-plugins-1.0.0.schema.json").read_bytes()))
    validate(identity["extensions"][NAMESPACE], read_json((SCHEMAS / "host-native.schema.json").read_bytes()))
    if any(release.get(key) != value for key, value in release_identity(identity, release_sequence=release["releaseSequence"]).items()) or digest(files["tool-preview.json"]) != release["toolCatalogDigest"] or digest(files["sbom.spdx.json"]) != release["sbomDigest"]:
        raise ValueError("rootReleaseMismatch")
    return portable_files(files, identity)


def generate_stable_view(artifact_root: Path, receipt: dict, output_root: Path, *, trust_policy=None, verified_context=None) -> Path:
    selected = approved_portable_files(artifact_root, receipt, trust_policy, verified_context)
    # Validate all input before touching an existing approved projection.
    output_root = regular_directory(output_root.absolute())
    target = output_root / "published" / receipt["release"]["packageId"]
    regular_directory(target)
    if target.exists():
        if snapshot_files(target) != selected:
            raise ValueError("immutableStableViewExists")
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".stable-view-", dir=target.parent) as temporary:
        staged = Path(temporary) / receipt["release"]["packageId"]
        for name, raw in selected.items():
            path = staged.joinpath(*safe_path(name))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        staged.rename(target)
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact_root", type=Path)
    parser.add_argument("receipt", type=Path)
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--verified-context", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    args = parser.parse_args()
    try:
        print(generate_stable_view(args.artifact_root, read_json(args.receipt.read_bytes()), args.output_root, trust_policy=read_json(args.policy.read_bytes()), verified_context=read_json(args.verified_context.read_bytes())))
    except Exception:
        parser.exit(1, "stableViewGenerationFailed\n")


if __name__ == "__main__":
    main()
