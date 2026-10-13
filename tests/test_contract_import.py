"""Contract copying and public-source boundaries; Python 3.12 standard library."""
import fnmatch
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from scripts.import_contracts import import_contracts

ROOT = Path(__file__).resolve().parents[1]


def text(path):
    return path.read_text(encoding="utf-8") if path.is_file() else ""


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
            inventory = json.loads((out / "owner-contracts.json").read_text())
            for source, (name, title, owner, version) in zip(sources, cases):
                self.assertEqual((out / name).read_bytes(), source.read_bytes())
                digest = hashlib.sha256(source.read_bytes()).hexdigest()
                self.assertEqual(hashes[name], digest)
                self.assertEqual(inventory["contracts"][name]["sha256"], digest)
                self.assertEqual(inventory["contracts"][name]["owner"], owner)
                self.assertEqual(inventory["contracts"][name]["version"], version)
            self.assertNotIn("plugins", inventory)
            checked_inventory = json.loads((ROOT / "schemas/owner-contracts.json").read_text())
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
            root_identity = json.loads(path.read_text())
            self.assertEqual(path.parent.name, root_identity["name"])
            self.assertTrue(root_identity["version"])
            self.assertTrue(root_identity["author"]["name"])
            if (ROOT / "published" / path.parent.name / "plugin.json").exists():
                self.assertEqual((ROOT / "published" / path.parent.name / "plugin.json").read_bytes(), path.read_bytes())
        contract_inventory = json.loads((ROOT / "schemas/owner-contracts.json").read_text())
        self.assertEqual(set(contract_inventory), {"schemaVersion", "contracts"})
        for record in contract_inventory["contracts"].values():
            self.assertEqual(set(record), {"owner", "source", "version", "sha256"})
        for path in ROOT.rglob("*.json"):
            if path.name == "plugin.json" or path.parent.name == "schemas":
                continue
            value = json.loads(path.read_text())
            self.assertFalse(isinstance(value, dict) and ("plugins" in value or "catalog" in value), str(path))

    def test_public_tree_excludes_credentials_runtime_data_and_models(self):
        # Catches accidental public material and missing ignore protection before stage.
        patterns = text(ROOT / ".gitignore").splitlines()
        for required in (".env", ".env.*", "credentials/", "*.key", "*.pem", "runtime/", "cache/", "dist/", "models/", "*.rf6", "*.3dm"):
            self.assertIn(required, patterns, f"Missing public-tree protection: {required}")
        prohibited_dirs = {"credentials", ".venv", "runtime", "cache", "dist", "models", "__pycache__"}
        prohibited_files = (".env", ".env.*", "*.key", "*.pem", "*.exe", "*.dll", "*.zip", "*.yak", "*.rf6", "*.3dm", "*.sqlite*", "*.log")
        for path in ROOT.rglob("*"):
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

    def test_published_is_generated_and_not_development_source(self):
        # Catches placing unapproved development roots into formal discovery.
        self.assertTrue((ROOT / "README.md").is_file(), "Stable-view policy is missing")
        self.assertFalse((ROOT / ".agents/plugins/marketplace.json").exists())
        self.assertFalse((ROOT / "published").exists(), "No approved Stable input in this skeleton")
        self.assertFalse((ROOT / "plugins/dlubal-rfem/mcp.json").exists())
        self.assertFalse((ROOT / "plugins/rhino/mcp.json").exists())


if __name__ == "__main__":
    unittest.main()
