"""One-time provenance and RFEM-only source acceptance, without runtime imports."""

import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "dlubal_rfem_mcp"
TOOLS = {
    "health_check",
    "get_rfem_application_info",
    "inspect_rfem_api",
    "write_rfem_model",
    "calculate_rfem_model",
    "run_rfem_script",
    "get_rfem_execution",
    "read_rfem_results",
}


class OriginBoundaryTests(unittest.TestCase):
    def test_verified_source_provenance(self):
        origin = (ROOT / "ORIGIN.md").read_text(encoding="utf-8")
        for required in (
            "1.0.14",
            "947fac2c183061a79bdb59ddab053a30aebd8ea7d23702202bf516c42c20b0b7",
            "2026-09-12",
            "independent",
        ):
            self.assertIn(required, origin)

    def test_complete_rfem_shared_core_source_and_eight_definitions(self):
        required = {
            "core/server.py",
            "core/auth.py",
            "core/config.py",
            "core/operation_control.py",
            "core/product_execution.py",
            "core/introspection/index.py",
            "core/introspection/render.py",
            "core/introspection/resolve.py",
            "rfem/session.py",
            "rfem/model_binding.py",
            "rfem/model_operations.py",
            "rfem/results.py",
            "rfem/execution.py",
            "rfem/tools.py",
            "rfem/state_fingerprint.py",
        }
        actual = {p.relative_to(SOURCE).as_posix() for p in SOURCE.rglob("*.py")}
        self.assertFalse(required - actual, required - actual)
        definitions = set()
        for relative in ("core/server.py", "rfem/model_operations.py", "rfem/tools.py"):
            tree = ast.parse((SOURCE / relative).read_text(encoding="utf-8"))
            definitions.update(
                node.name
                for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            )
        self.assertEqual(TOOLS & definitions, TOOLS)

    def test_other_products_are_excluded(self):
        self.assertTrue(SOURCE.is_dir())
        for product in ("rstab", "rsection"):
            self.assertFalse((SOURCE / product).exists())
            for path in SOURCE.rglob("*.py"):
                self.assertIsNone(
                    re.search(
                        rf"(?<![a-z]){product}(?![a-z])",
                        path.read_text(encoding="utf-8").lower(),
                    ),
                    path,
                )


if __name__ == "__main__":
    unittest.main()
