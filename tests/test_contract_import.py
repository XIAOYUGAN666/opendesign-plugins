"""Contract copying and public-source boundaries; Python 3.12 standard library."""
import fnmatch
import hashlib
import json
import subprocess
from pathlib import Path
import tempfile
import unittest
from scripts.import_contracts import import_contracts

ROOT = Path(__file__).resolve().parents[1]


def text(path):
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def public_candidates(root):
    """Tracked files plus explicit unignored source candidates; ignored artifacts stay private.

    --cached keeps an accidentally force-added secret/artifact visible to checks.
    --others --exclude-standard checks prospective public source before staging.
    """
    names = subprocess.check_output(
        ["git", "-C", str(root), "ls-files", "--cached", "--others", "--exclude-standard", "-z"]
    ).decode("utf-8").split("\0")
    return [root / name for name in sorted(set(names)) if name and (root / name).is_file()]


class ContractImportTests(unittest.TestCase):
    def test_contract_import_is_byte_exact(self):
        # Catches serialization/newline drift, missing optional copies and stale inventory.
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            cases = [
                ("host-native.schema.json", "NativeExtensionV1", "host", "1"),
                ("agent-control.schema.json", "PluginRuntimeContractV2", "agent", "2"),
                ("host-contract.v3.schema.json", "HostContractV3", "host", "3"),
                ("plugin-configuration.schema.json", "PluginConfigurationV1", "host", "1"),
                ("agent-plugins-1.0.0.schema.json", "Agent Plugins Manifest", "agent-plugins", "1.0.0"),
            ]
            sources = []
            for i, (_, title, _, _) in enumerate(cases):
                source = base / f"input-{i}.json"
                source.write_bytes(json.dumps({"title": title, "$id": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"}, indent=3).encode() + b"\r\n")
                sources.append(source)
            out = base / "schemas"
            hashes = import_contracts(*sources[:2], out, host_contract_schema=sources[2], configuration_schema=sources[3], official_schema=sources[4])
            self.assertEqual(set(hashes), {case[0] for case in cases})
            inventory = json.loads((out / "owner-contracts.json").read_text(encoding="utf-8"))
            for source, (name, title, owner, version) in zip(sources, cases):
                self.assertEqual((out / name).read_bytes(), source.read_bytes())
                digest = hashlib.sha256(source.read_bytes()).hexdigest()
                self.assertEqual(hashes[name], digest)
                self.assertEqual(inventory["contracts"][name]["sha256"], digest)
                self.assertEqual(inventory["contracts"][name]["owner"], owner)
                self.assertEqual(inventory["contracts"][name]["version"], version)
            self.assertNotIn("plugins", inventory)
            checked_inventory = json.loads((ROOT / "schemas/owner-contracts.json").read_text(encoding="utf-8"))
            attributes = text(ROOT / ".gitattributes").splitlines()
            for name, record in checked_inventory["contracts"].items():
                self.assertEqual(hashlib.sha256((ROOT / "schemas" / name).read_bytes()).hexdigest(), record["sha256"])
                self.assertIn(f"/schemas/{name} -text", attributes)
            legacy = base / "legacy"
            self.assertEqual(set(import_contracts(*sources[:2], legacy)), {case[0] for case in cases[:2]})

    def test_no_root_python_environment(self):
        # Catches introducing shared Python dependency/runtime ownership.
        self.assertTrue((ROOT / "AGENTS.md").is_file(), "Independent-environment boundary is missing")
        for name in ("pyproject.toml", "uv.lock", ".venv", "requirements.txt"):
            self.assertFalse((ROOT / name).exists(), name)
        patterns = text(ROOT / ".gitignore").splitlines()
        self.assertIn(".venv/", patterns)
        self.assertIn("__pycache__/", patterns)

    def test_plugin_identity_has_one_human_source(self):
        # Catches missing roots, identity catalogs and generation that overrides roots.
        roots = list((ROOT / "plugins").glob("*/plugin.json"))
        self.assertEqual(len(roots), 2, "Development identities are missing")
        for path in roots:
            root_identity = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(path.parent.name, root_identity["name"])
            self.assertTrue(root_identity["version"])
            self.assertTrue(root_identity["author"]["name"])
        contract_inventory = json.loads((ROOT / "schemas/owner-contracts.json").read_text(encoding="utf-8"))
        self.assertEqual(set(contract_inventory), {"schemaVersion", "contracts"})
        for record in contract_inventory["contracts"].values():
            self.assertEqual(set(record), {"owner", "source", "version", "sha256"})
        for path in public_candidates(ROOT):
            if path.suffix != ".json":
                continue
            if path.name == "plugin.json" or path.parent.name == "schemas":
                continue
            value = json.loads(path.read_text(encoding="utf-8"))
            if path == ROOT / ".agents/plugins/marketplace.json":
                from scripts.generate_marketplace import marketplace_document
                # The one generated official discovery array is not a manual identity catalog.
                self.assertEqual(value, marketplace_document([]))
                continue
            self.assertFalse(isinstance(value, dict) and ("plugins" in value or "catalog" in value), str(path))

    def test_plugin_identity_reads_utf8_json_under_windows_ansi_defaults(self):
        # Exercise the real public JSON scan with the default encoding used by Windows CI.
        from unittest.mock import patch

        original_read_text = Path.read_text

        def read_with_ansi_default(path, encoding=None, errors=None):
            return original_read_text(path, encoding=encoding or "cp1252", errors=errors)

        with patch.object(Path, "read_text", read_with_ansi_default):
            try:
                self.test_plugin_identity_has_one_human_source()
            except UnicodeError as error:
                self.fail(f"Public JSON must be read as UTF-8 under Windows ANSI defaults: {error}")

    def test_public_tree_excludes_credentials_runtime_data_and_models(self):
        # Catches accidental public material and missing ignore protection before stage.
        patterns = text(ROOT / ".gitignore").splitlines()
        for required in (".env", ".env.*", "credentials/", "*.key", "*.pem", "runtime/", "cache/", "dist/", "models/", "*.rf6", "*.3dm"):
            self.assertIn(required, patterns, f"Missing public-tree protection: {required}")
        prohibited_dirs = {"credentials", ".venv", "runtime", "cache", "dist", "models", "__pycache__"}
        prohibited_files = (".env", ".env.*", "*.key", "*.pem", "*.exe", "*.dll", "*.zip", "*.yak", "*.rf6", "*.3dm", "*.sqlite*", "*.log")
        for path in public_candidates(ROOT):
            rel = path.relative_to(ROOT)
            if ".git" in rel.parts:
                continue
            self.assertFalse(prohibited_dirs.intersection(rel.parts), str(rel))
            self.assertFalse(any(fnmatch.fnmatch(path.name.lower(), pattern) for pattern in prohibited_files), str(rel))
            if path.is_file():
                data = path.read_bytes()
                self.assertNotIn(b"-----BEGIN " + b"PRIVATE KEY-----", data, str(rel))
                self.assertNotIn(b"-----BEGIN " + b"OPENSSH PRIVATE KEY-----", data, str(rel))
                self.assertNotIn(bytes([0]), data, str(rel))

    def test_public_candidates_allow_ignored_provider_environment_but_scan_tracked_and_untracked_source(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            subprocess.run(["git", "init", "-q", str(base)], check=True)
            (base / ".gitignore").write_text(".venv/\ndist/\ncache/\n*.pem\n")
            for name in ("provider/.venv/python.exe", "provider/dist/a.zip", "provider/cache/a.json", "provider/test_fixture.py", "provider/credentials.pem"):
                path = base / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("source")
            subprocess.run(["git", "-C", str(base), "add", "-f", "provider/credentials.pem"], check=True)
            candidates = {path.relative_to(base).as_posix() for path in public_candidates(base)}
            self.assertEqual(candidates, {".gitignore", "provider/test_fixture.py", "provider/credentials.pem"})

    def test_published_is_generated_and_not_development_source(self):
        # Catches placing unapproved development roots into formal discovery.
        self.assertTrue((ROOT / "README.md").is_file(), "Stable-view policy is missing")
        from scripts.generate_marketplace import generate_marketplace
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "marketplace.json"
            generate_marketplace([], output, view_root=ROOT)
            self.assertEqual((ROOT / ".agents/plugins/marketplace.json").read_bytes(), output.read_bytes())
        self.assertFalse((ROOT / "published").exists(), "No approved Stable input in this skeleton")
        self.assertFalse((ROOT / ".agents/plugins/marketplace.snapshot.json").exists(), "No signed Stable snapshot yet")
        self.assertIn("/published/** -text", text(ROOT / ".gitattributes").splitlines())
        self.assertFalse((ROOT / "plugins/dlubal-rfem/mcp.json").exists())
        self.assertFalse((ROOT / "plugins/rhino/mcp.json").exists())


if __name__ == "__main__":
    unittest.main()
