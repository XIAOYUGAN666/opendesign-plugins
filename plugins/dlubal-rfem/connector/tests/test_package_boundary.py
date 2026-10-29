"""Reject accidental legacy, cross-project or official-distribution coupling."""

import ast
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class PackageBoundaryTests(unittest.TestCase):
    def test_independent_frozen_project(self):
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(project["project"]["name"], "dlubal-rfem-mcp")
        self.assertEqual(project["project"]["requires-python"], ">=3.12,<3.13")
        dependencies = project["project"]["dependencies"]
        for dependency in ("fastmcp==4.0.3", "mcp==2.2.0", "dlubal.api==2.14.13"):
            self.assertIn(dependency, dependencies)
        self.assertFalse(any("dlubal-mcp" in value for value in dependencies))

    def test_source_imports_only_own_package_and_external_dependencies(self):
        paths = list((ROOT / "dlubal_rfem_mcp").rglob("*.py"))
        self.assertTrue(paths, "Connector source is absent")
        for path in paths:
            source = path.read_text(encoding="utf-8")
            for forbidden in (
                "mcp-providers",
                "agent-server",
                "api-server",
                "sys.path",
            ):
                self.assertNotIn(forbidden, source, path)
            for node in ast.walk(ast.parse(source)):
                modules = []
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    modules = [node.module]
                for module in modules:
                    self.assertNotIn(
                        module.split(".")[0],
                        {
                            "dlubal_mcp",
                            "apps",
                            "app",
                            "agent",
                            "api_server",
                            "rstab",
                            "rsection",
                        },
                        (path, module),
                    )

    def test_lock_has_only_modern_mcp_and_no_cross_project_source(self):
        lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
        packages = {package["name"]: package for package in lock["package"]}
        for name, version in (
            ("fastmcp", "4.0.3"),
            ("mcp", "2.2.0"),
            ("dlubal-api", "2.14.13"),
        ):
            self.assertEqual(packages[name]["version"], version)
        self.assertNotIn("dlubal-mcp", packages)
        for package in packages.values():
            source = package["source"]
            if "editable" in source:
                self.assertEqual(source["editable"], ".")
                self.assertEqual(package["name"], "dlubal-rfem-mcp")
            self.assertNotIn("directory", source)


if __name__ == "__main__":
    unittest.main()
