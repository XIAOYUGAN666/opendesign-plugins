"""The two connectors receive identical, owner-derived wire validation."""
import json
import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ConnectorCodecGenerationTests(unittest.TestCase):
    def test_identical_generated_codec_binds_exact_host_schema(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            schema = root / "schema.json"
            schema.write_text(json.dumps({"title": "ConnectorWireContractV1", "type": "object", "properties": {"kind": {"const": "ensureSoftwareConnection"}}, "required": ["kind"], "additionalProperties": False}), encoding="utf-8")
            bootstrap = root / "bootstrap.json"
            bootstrap.write_text(json.dumps({"title":"ConnectorBootstrapV3", "type":"object", "properties":{"schemaVersion":{"const":3}}, "required":["schemaVersion"], "additionalProperties":False}), encoding="utf-8")
            ready = root / "ready.json"
            ready.write_text(json.dumps({"title":"ConnectorReadyV3", "type":"object", "properties":{"kind":{"const":"ready"}}, "required":["kind"]}), encoding="utf-8")
            first, second = root / "first.py", root / "second.py"
            subprocess.run([sys.executable, "-B", str(ROOT / "scripts/generate_connector_control.py"), str(schema), str(first), str(second), "--bootstrap-schema", str(bootstrap), "--ready-schema", str(ready)], check=True)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertIn(b"owner_schema_sha256", first.read_bytes())
            spec = importlib.util.spec_from_file_location("connector_codec_test", first)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self.assertEqual(module.decode_control(b'{"kind":"ensureSoftwareConnection"}')["kind"], "ensureSoftwareConnection")
            self.assertEqual(module.decode_bootstrap(b'{"schemaVersion":3}')["schemaVersion"], 3)
            self.assertEqual(module.bootstrap_schema()["title"], "ConnectorBootstrapV3")
            self.assertIn("ConnectorReadyV3", module.lifecycle_schema({"type":"object"})["$defs"])
            with self.assertRaises(ValueError):
                module.decode_bootstrap(b'{"schemaVersion":2}')
            for raw in (b'{"kind":"x"}', b'{"kind":"ensureSoftwareConnection","extra":1}', b'{"kind":"ensureSoftwareConnection","kind":"ensureSoftwareConnection"}', b' ' * 16385):
                with self.assertRaises(ValueError):
                    module.decode_control(raw)

    def test_external_schema_references_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            schema = root / "schema.json"
            schema.write_text(json.dumps({"title": "ConnectorWireContractV1", "$ref": "https://example.com/schema"}), encoding="utf-8")
            bootstrap = root / "bootstrap.json"
            bootstrap.write_text(json.dumps({"title":"ConnectorBootstrapV3"}), encoding="utf-8")
            ready = root / "ready.json"
            ready.write_text(json.dumps({"title":"ConnectorReadyV3"}), encoding="utf-8")
            output = root / "out.py"
            result = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/generate_connector_control.py"), str(schema), str(output), "--bootstrap-schema", str(bootstrap), "--ready-schema", str(ready)], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
