"""Check CI dependency requests against each independent project's contract."""
import re
import tomllib
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class CiContractTests(unittest.TestCase):
    def test_native_provider_ci_builds_its_owned_test_host_before_python_tests(self):
        workflow = (ROOT / ".github/workflows/verify.yml").read_text(encoding="utf-8")
        provider_job = workflow.split("\n  connector:\n", 1)[1]
        build = "dotnet test native/tests/RhinoMcpBridge.Tests.csproj -c Release -p:RestoreLockedMode=true"
        self.assertIn(build, provider_job, "Real process interoperability requires the native host to be built, not skipped")
        both_targets = "dotnet build native/RhinoMcpBridge.csproj -c Release -p:RestoreLockedMode=true"
        self.assertIn(both_targets, provider_job, "A fresh runner must build both Bridge TFMs before identity tests")
        self.assertLess(provider_job.index(both_targets), provider_job.index(build))
        self.assertLess(provider_job.index(build), provider_job.index("uv run --frozen --extra test python -B -m pytest -q"))
        self.assertIn("working-directory: plugins/${{ matrix.plugin }}\n", provider_job)
        self.assertRegex(provider_job, r"actions/setup-dotnet@[a-f0-9]{40}")

    def test_verify_sync_requests_only_extras_owned_by_each_project(self):
        # Catches reusing fixture-only extras for a migrated real Connector.
        workflow = (ROOT / ".github/workflows/verify.yml").read_text(encoding="utf-8")
        plugins = re.search(r"^        plugin: \[([^\]]+)\]$", workflow, re.MULTILINE)
        self.assertIsNotNone(plugins, "Connector matrix must be checked")
        plugin_ids = [value.strip() for value in plugins.group(1).split(",")]
        steps = re.findall(
            r"^        working-directory: (.+)\n"
            r"        shell: pwsh\n"
            r"(?:        env:\n(?:          .*\n)+)?"
            r"        run: \|\n((?:          .*\n)+)",
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
                        if project_directory == "tests/fixtures/fixture-engineering/connector":
                            self.assertIn("build", extras, "Fixture artifact tooling requires its build extra")
        self.assertEqual(sync_count, len(re.findall(r"\buv sync\b", workflow)))
        self.assertGreater(sync_count, 0, "No CI project sync was checked")

    def test_manual_candidate_workflow_verifies_real_packages_without_publishing_them(self):
        workflow = (ROOT / ".github/workflows/release-plugin.yml").read_text(encoding="utf-8")

        self.assertIn("scripts/build_artifact.py", workflow)
        self.assertIn("scripts/verify_artifact.py", workflow)
        self.assertIn("--unsigned", workflow)
        self.assertIn("only synthetic fixture artifacts are uploaded", workflow)
        self.assertIn("if: success() && inputs.plugin == 'fixture-engineering'", workflow)
        self.assertNotIn("Real Connector release builder has not migrated", workflow)
        self.assertNotIn("gh release create", workflow)
        candidate_job = workflow.split("\n  candidate:\n", 1)[1].split("\n  signing:\n", 1)[0]
        self.assertNotIn("${{ secrets.", candidate_job)

    def test_signing_is_manual_main_only_environment_bound_and_has_no_publication_operation(self):
        workflow = (ROOT / ".github/workflows/release-plugin.yml").read_text(encoding="utf-8")
        self.assertIn("environment: plugin-signing", workflow)
        self.assertIn("github.ref == 'refs/heads/main'", workflow)
        self.assertIn("RFEM_PUBLISHER_V1_KEY_B64: ${{ secrets.RFEM_PUBLISHER_V1_KEY_B64 }}", workflow)
        self.assertIn("RHINO_PUBLISHER_V1_KEY_B64: ${{ secrets.RHINO_PUBLISHER_V1_KEY_B64 }}", workflow)
        self.assertIn("scripts/publisher_signing.py", workflow)
        signing = workflow.split("\n  signing:\n", 1)[1]
        self.assertIn("permissions:\n      contents: write", signing)
        self.assertIn("persist-credentials: false", signing)
        self.assertNotIn("--method", signing)
        self.assertNotIn("git push", signing)
        self.assertNotIn("gh api --input", signing)
        self.assertNotIn("gh release create", workflow)
        self.assertNotIn("gh release edit", workflow)

    def test_market_review_signing_stays_isolated_from_formal_market_and_binary_artifacts(self):
        workflow = (ROOT / ".github/workflows/release-plugin.yml").read_text(encoding="utf-8")
        self.assertIn("sign-market-candidate", workflow)
        self.assertIn("--market-inputs", workflow)
        self.assertIn("market-review", workflow)
        self.assertIn("rhino_archive_sha256", workflow)
        self.assertNotIn("gh release upload", workflow)
        self.assertNotIn("git push", workflow)
        self.assertNotIn("path: published", workflow)

    def test_draft_lookup_does_not_use_the_published_release_by_tag_endpoint(self):
        # An unpublished draft can be listable/by-ID without a published tag.
        workflow = (ROOT / ".github/workflows/release-plugin.yml").read_text(encoding="utf-8")
        self.assertNotIn("/releases/tags/", workflow)
        self.assertIn("releases?per_page=100", workflow)
        self.assertIn("$drafts.Count -ne 1", workflow)


if __name__ == "__main__":
    unittest.main()
