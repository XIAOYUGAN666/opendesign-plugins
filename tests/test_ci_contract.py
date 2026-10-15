"""Check CI dependency requests against each independent project's contract."""
import re
import tomllib
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class CiContractTests(unittest.TestCase):
    def test_verify_sync_requests_only_extras_owned_by_each_project(self):
        # Catches reusing fixture-only extras for a migrated real Provider.
        workflow = (ROOT / ".github/workflows/verify.yml").read_text(encoding="utf-8")
        plugins = re.search(r"^        plugin: \[([^\]]+)\]$", workflow, re.MULTILINE)
        self.assertIsNotNone(plugins, "Provider matrix must be checked")
        plugin_ids = [value.strip() for value in plugins.group(1).split(",")]
        steps = re.findall(
            r"^        working-directory: (.+)\n"
            r"        shell: pwsh\n        run: \|\n((?:          .*\n)+)",
            workflow,
            re.MULTILINE,
        )
        sync_count = 0
        for directory, script in steps:
            for command in re.findall(r"^          (uv sync .+)$", script, re.MULTILINE):
                sync_count += 1
                extras = set(re.findall(r"--extra\s+([\w-]+)", command))
                self.assertIn("--frozen", command)
                self.assertIn("test", extras)
                directories = (
                    [directory.replace("${{ matrix.plugin }}", plugin) for plugin in plugin_ids]
                    if "${{ matrix.plugin }}" in directory else [directory]
                )
                for project_directory in directories:
                    project = ROOT / project_directory / "pyproject.toml"
                    if not project.is_file():
                        # The workflow explicitly reports unmigrated matrix projects notPresent.
                        self.assertIn("${{ matrix.plugin }}", directory)
                        continue
                    with project.open("rb") as stream:
                        declared = tomllib.load(stream)["project"].get("optional-dependencies", {})
                    with self.subTest(project=project_directory):
                        self.assertFalse(
                            extras - declared.keys(),
                            f"{project_directory}: CI requests undefined extras {sorted(extras - declared.keys())}",
                        )
                        if project_directory == "tests/fixtures/fixture-engineering/provider":
                            self.assertIn("build", extras, "Fixture artifact tooling requires its build extra")
        self.assertEqual(sync_count, len(re.findall(r"\buv sync\b", workflow)))
        self.assertGreater(sync_count, 0, "No CI project sync was checked")


if __name__ == "__main__":
    unittest.main()
