"""Use a verified candidate runtime to run the independent native fixture controls."""

import os
import subprocess
from pathlib import Path

import pytest


def test_upstream_native_geometry_controls(tmp_path):
    candidate = os.environ.get("RHINO_LIVE_CANDIDATE")
    port = os.environ.get("RHINO_LIVE_TEST_PORT")
    if not candidate or not port:
        pytest.skip("requires a verified candidate and dedicated Windows Rhino instance")
    plugin_root = Path(__file__).resolve().parents[3]
    python = (Path(candidate) / "runtime/python/python.exe").resolve(strict=True)
    subprocess.run(
        [
            str(python), "-B", "-c",
            'import runpy,sys;sys.path.insert(0,sys.argv.pop(1));'
            'runpy.run_module("experiments.validate_live",run_name="__main__")',
            str(plugin_root), "--bridge-port", str(int(port)), "--output", str(tmp_path),
        ],
        cwd=plugin_root, check=True, timeout=180,
    )
    assert len(list(tmp_path.glob("evaluator-*/validation.json"))) == 3
