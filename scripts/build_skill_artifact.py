"""Build an unsigned, deterministic skill-only candidate outside the repository."""
import argparse
import re
import sys
import time
import zipfile
from pathlib import Path

try:
    from .verify_artifact import canonical, digest, read_json, safe_path, validate
except ImportError:
    from verify_artifact import canonical, digest, read_json, safe_path, validate

ROOT = Path(__file__).resolve().parents[1]
SKILL = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
PACKAGE = re.compile(r"[a-z][a-z0-9.-]{0,126}[a-z0-9]\Z")
VERSION = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\Z")


def capture(root):
    if root.is_symlink() or root.is_junction() or not root.is_dir():
        raise ValueError("invalidSource")
    root = root.resolve(strict=True)
    pending, folded, total = [], set(), 0
    for path in root.rglob("*"):
        if path.is_symlink() or path.is_junction():
            raise ValueError("invalidSource")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError("invalidSource")
        name = path.relative_to(root).as_posix()
        safe_path(name)
        if name.casefold() in folded or len(name) > 255 or len(name.split("/")) > 9:
            raise ValueError("invalidSource")
        if name not in {"plugin.json", "README.md"} and not name.startswith("skills/"):
            raise ValueError("invalidSource")
        if path.suffix.lower() in {".exe", ".dll", ".pyd", ".so", ".dylib", ".zip", ".yak", ".key", ".pem"}:
            raise ValueError("invalidSource")
        folded.add(name.casefold())
        size = path.stat().st_size
        total += size
        if size > 8 * 1024 * 1024 or (name.endswith("/SKILL.md") and size > 512 * 1024) or total > 8 * 1024 * 1024 or len(pending) >= 256:
            raise ValueError("invalidSource")
        pending.append((name, path, size))
    files = {}
    for name, path, size in sorted(pending):
        with path.open("rb") as file:
            data = file.read(size + 1)
        if len(data) != size:
            raise ValueError("sourceChanged")
        files[name] = data
    return files


def validate_files(files):
    if "plugin.json" not in files or not 0 < len(files) <= 256 or sum(map(len, files.values())) > 8 * 1024 * 1024:
        raise ValueError("invalidSource")
    for name, content in files.items():
        safe_path(name)
        if len(name) > 255 or len(name.split("/")) > 9 or (name not in {"plugin.json", "README.md"} and not name.startswith("skills/")):
            raise ValueError("invalidSource")
        if Path(name).suffix.lower() in {".exe", ".dll", ".pyd", ".so", ".dylib", ".zip", ".yak", ".key", ".pem"}:
            raise ValueError("invalidSource")
        if name.endswith("/SKILL.md") and len(content) > 512 * 1024:
            raise ValueError("invalidSource")
    identity = read_json(files["plugin.json"])
    validate(identity, read_json((ROOT / "schemas/agent-plugins-1.0.0.schema.json").read_bytes()))
    if not PACKAGE.fullmatch(identity.get("name", "")) or not VERSION.fullmatch(identity.get("version", "")) or "io.github.xiaoyugan666.native" in identity.get("extensions", {}):
        raise ValueError("invalidIdentity")
    skill_names = set()
    for path, content in files.items():
        if not path.startswith("skills/"):
            continue
        parts = path.split("/")
        if len(parts) < 3 or not SKILL.fullmatch(parts[1]) or f"skills/{parts[1]}/SKILL.md" not in files:
            raise ValueError("invalidSkill")
        if path.endswith("/SKILL.md") and len(parts) == 3:
            lines = content.decode("utf-8").splitlines()
            if len(lines) < 4 or lines[0] != "---" or "---" not in lines[1:]:
                raise ValueError("invalidSkill")
            frontmatter = lines[1:lines.index("---", 1)]
            if [line for line in frontmatter if line.startswith("name: ")] != [f"name: {parts[1]}"] or not any(line.startswith("description: ") and line[13:].strip() for line in frontmatter):
                raise ValueError("invalidSkill")
            skill_names.add(parts[1])
    if not skill_names:
        raise ValueError("invalidSkill")
    return identity


def build(root, output, sequence):
    root = root.absolute()
    if root.is_symlink() or root.is_junction():
        raise ValueError("invalidSource")
    resolved_root = root.resolve(strict=True)
    output = output.resolve()
    if output.exists() or output.is_relative_to(ROOT) or output.is_relative_to(resolved_root) or not 0 < sequence < 2**64:
        raise ValueError("invalidOutput")
    files = capture(root)
    if files != capture(root):
        raise ValueError("sourceChanged")
    identity = validate_files(files)
    inventory = [{"path": name, "byteLength": len(data), "sha256": digest(data)} for name, data in files.items()]
    output.mkdir(parents=True, exist_ok=False)
    archive = output / f"{identity['name']}-{identity['version']}-skills.zip"
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zipped:
        for name, data in files.items():
            entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o100644 << 16
            zipped.writestr(entry, data, compresslevel=9)
    now = int(time.time())
    receipt = {"release": {"packageId": identity["name"], "pluginVersion": identity["version"], "releaseSequence": sequence, "filesDigest": digest(canonical(inventory))},
               "archive": {"url": "https://example.invalid/internal-fixture/" + archive.name, "byteLength": archive.stat().st_size, "sha256": digest(archive.read_bytes())},
               "publisherKeyId": "internal-fixture-test", "notBefore": now - 60, "expiresAt": now + 3600, "revoked": False}
    (output / "unsigned-receipt.json").write_bytes(canonical(receipt))
    (output / "inventory.json").write_bytes(canonical(inventory))
    return archive


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plugin_root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--release-sequence", type=int, required=True)
    args = parser.parse_args()
    try:
        print(build(args.plugin_root, args.output, args.release_sequence))
    except Exception:
        parser.exit(1, "skillArtifactBuildFailed\n")


if __name__ == "__main__":
    main()
