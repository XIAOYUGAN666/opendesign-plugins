import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ConnectorProcessTests(unittest.TestCase):
    def test_process_identity_changes_on_pid_reuse_and_is_opaque(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            first, second = root / "a.py", root / "b.py"
            subprocess.run([sys.executable, "-B", str(ROOT / "scripts/generate_connector_process.py"), str(first), str(second)], check=True)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            spec = importlib.util.spec_from_file_location("test_connector_process", first)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            initial = module.select_instance([("RFEM6.exe", 1234, 100)], "rfem6.exe")
            reused = module.select_instance([("RFEM6.exe", 1234, 101)], "rfem6.exe")
            self.assertTrue(initial.startswith("si_"))
            self.assertNotEqual(initial, reused)
            self.assertNotIn("1234", initial)
            self.assertIsNone(module.select_instance([("Rhino.exe", 1234, 100)], "RFEM6.exe"))
            with self.assertRaisesRegex(OSError, "multipleSoftwareInstances"):
                module.select_instance([("RFEM6.exe", 1234, 100), ("RFEM6.exe", 1235, 101)], "RFEM6.exe")
            if sys.platform == "win32":
                self.assertIsNone(module.running_instance("fixture-never-a-real-process.exe"))
