"""The synthetic Connector can build a connector with no skill directory."""
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROVIDER = ROOT / "tests/fixtures/fixture-engineering/connector"
PYTHON = PROVIDER / ".venv/Scripts/python.exe"


class ConnectorOnlyStageTests(unittest.TestCase):
    def test_fixture_stage_without_skills(self):
        code = r'''
import os, shutil, sys
from pathlib import Path
import scripts.build_fixture as builder

original = builder.ROOT
target = Path(sys.argv[1])
plugin = target / "plugin"
connector = plugin / "connector"
connector.mkdir(parents=True)
shutil.copyfile(original.parent / "plugin.json", plugin / "plugin.json")
for name in ("pyproject.toml", "uv.lock"):
    shutil.copyfile(original / name, connector / name)
shutil.copytree(original / "fixture_engineering", connector / "fixture_engineering")
original_download = builder.download
builder.download = lambda record, cache: original_download(record, original / "cache/downloads")
builder.ROOT = connector.resolve()
staged = builder.build_fixture(plugin, target / "output")
assert (staged / "plugin.json").is_file()
assert (staged / "tool-preview.json").is_file()
assert not (staged / "skills").exists()
sys.path.insert(0, str(original.parents[3] / "scripts"))
from build_artifact import _finish_staged_artifact
environment = {k: os.environ[k] for k in ("SystemRoot", "WINDIR", "TEMP", "TMP", "PATH", "USERPROFILE", "LOCALAPPDATA", "APPDATA") if k in os.environ}
archive = _finish_staged_artifact(plugin, target / "output", original / ".venv/Scripts/python.exe", environment, True, release_sequence=2)
assert archive.is_file()
assert (target / "output/unsigned-receipt.json").is_file()
'''
        with tempfile.TemporaryDirectory(prefix="connector-stage-") as directory:
            result = subprocess.run(
                [str(PYTHON), "-B", "-c", code, directory], cwd=PROVIDER,
                capture_output=True, timeout=240,
            )
            self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
