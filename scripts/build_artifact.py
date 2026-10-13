"""Standard-library release orchestration; fixture runtime stays independent."""
import argparse
from pathlib import Path
import subprocess
import os
import re
import time
import zipfile

try:
    from .verify_artifact import canonical, digest, discover, read_json, release_identity
except ImportError:
    from verify_artifact import canonical, digest, discover, read_json, release_identity

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/fixture-engineering"


def generated_input(plugin_root):
    data = (plugin_root / "plugin.json").read_bytes()
    return {"generatedFrom": "../plugin.json", "rootSha256": digest(data), "releaseIdentity": release_identity(read_json(data))}


def check_source_closure(plugin_root):
    def check(path):
        if path.is_symlink() or path.is_junction():
            raise ValueError("reparseSource")
        if not path.exists():
            raise ValueError("missingSource")
    for path in (plugin_root, plugin_root / "plugin.json", plugin_root / "provider"):
        check(path)
    identity = read_json((plugin_root / "plugin.json").read_bytes())
    module = identity["extensions"]["io.github.xiaoyugan666.native"]["module"].split(".")
    if any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", part) for part in module):
        raise ValueError("unsafeProviderModule")
    copied = [plugin_root / "skills", plugin_root / "provider" / module[0], plugin_root / "provider/pyproject.toml", plugin_root / "provider/uv.lock"]
    for source in copied:
        check(source)
        if source.is_dir():
            for path in source.rglob("*"):
                check(path)


def build_artifact(plugin_root: Path, output_dir: Path) -> Path:
    plugin_root = plugin_root.resolve(strict=True)
    output_dir = output_dir.resolve()
    if plugin_root != FIXTURE.resolve():
        raise ValueError("providerBuilderNotMigrated")
    if output_dir.is_relative_to(ROOT) or output_dir.exists():
        raise ValueError("outputMustBeFreshAndOutsideRepository")
    check_source_closure(plugin_root)
    tool_python = plugin_root / "provider/.venv/Scripts/python.exe"
    environment = {k: os.environ[k] for k in ("SystemRoot", "WINDIR", "TEMP", "TMP", "PATH", "USERPROFILE", "LOCALAPPDATA", "APPDATA") if k in os.environ}
    subprocess.run([str(tool_python), "-B", "-m", "scripts.build_fixture", "--output", str(output_dir)], cwd=plugin_root / "provider", env=environment, check=True, capture_output=True, timeout=180)
    staged = output_dir / "artifact"
    identity = read_json((staged / "plugin.json").read_bytes())
    if (staged / "plugin.json").read_bytes() != (plugin_root / "plugin.json").read_bytes():
        raise ValueError("rootChangedDuringBuild")
    release = release_identity(identity)
    release["providerBuildId"] = read_json((staged / "fixture-build.json").read_bytes())["providerBuildId"]
    # The public startup/MCP probe needs only this provisional release identity.
    (staged / "release.json").write_bytes(canonical(release))
    observed = discover(staged)
    preview = observed["preview"]
    if preview != read_json((staged / "tool-preview.json").read_bytes()):
        raise ValueError("typedStagedCatalogMismatch")
    (staged / "tool-preview.json").write_bytes(canonical(preview))
    (staged / "README.md").write_bytes(
        ("# Internal fixture\n\nSynthetic test artifact only; never authorizes Stable publication.\n"
         "Standard clients may read workflow skills. Native MCP tools require the compatible Windows Host; "
         "metadata mode lists tools and refuses execution. No engineering software or credential is used.\n").encode()
    )
    files = [{"path": p.relative_to(staged).as_posix(), "byteLength": p.stat().st_size, "sha256": digest(p.read_bytes())} for p in sorted(staged.rglob("*")) if p.is_file() and p.relative_to(staged).as_posix() not in {"files.json", "release.json"}]
    inventory = canonical({"schemaVersion": 1, "files": files})
    (staged / "files.json").write_bytes(inventory)
    release.update(toolCatalogDigest=digest(canonical(preview)), filesDigest=digest(inventory), sbomDigest=digest((staged / "sbom.spdx.json").read_bytes()))
    (staged / "release.json").write_bytes(canonical(release))
    archive_path = output_dir / f"{identity['name']}-{identity['version']}-{release['artifactVariantId']}.zip"
    with zipfile.ZipFile(archive_path, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(staged.rglob("*")):
            if path.is_file():
                info = zipfile.ZipInfo(path.relative_to(staged).as_posix(), date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, path.read_bytes(), compresslevel=9)
    now = int(time.time())
    candidate = {"release": release, "archive": {"url": "https://example.invalid/internal-fixture/" + archive_path.name, "byteLength": archive_path.stat().st_size, "sha256": digest(archive_path.read_bytes())}, "publisherKeyId": "internal-fixture-test", "notBefore": now - 60, "expiresAt": now + 3600, "revoked": False}
    receipt = output_dir / "unsigned-receipt.json"
    receipt.write_bytes(canonical(candidate))
    # Independently extract final bytes in the fixture's tool environment.
    subprocess.run([str(tool_python), "-B", str(ROOT / "scripts/verify_artifact.py"), str(archive_path), str(receipt), str(ROOT / "schemas"), "--unsigned"], cwd=ROOT, env=environment, check=True, capture_output=True, timeout=120)
    return archive_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plugin_root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--generate-release-input", action="store_true")
    args = parser.parse_args()
    try:
        if args.generate_release_input:
            target = args.plugin_root / "packaging/release-input.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(canonical(generated_input(args.plugin_root)))
            print(target)
        else:
            if not args.output:
                parser.error("--output required")
            print(build_artifact(args.plugin_root, args.output))
    except Exception:
        parser.exit(1, "artifactBuildFailed\n")


if __name__ == "__main__":
    main()
