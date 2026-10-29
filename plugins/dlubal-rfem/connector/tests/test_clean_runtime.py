import os
from pathlib import Path

import pytest

from scripts.verify_plugin import verify


@pytest.mark.skipif(
    "RFEM_PLUGIN_ARTIFACT" not in os.environ,
    reason="set RFEM_PLUGIN_ARTIFACT to run the isolated artifact gate",
)
def test_built_artifact_uses_only_its_private_runtime():
    result = verify(Path(os.environ["RFEM_PLUGIN_ARTIFACT"]).resolve())

    executable = Path(result["runtime"]["executable"])
    assert executable.name.lower() == "python.exe"
    assert result["runtime"]["isolated"] == 1
    assert result["runtime"]["architecture"] == "AMD64"
    assert result["runtime"]["fastmcp"] == "4.0.3"
    assert result["runtime"]["mcp"] == "2.2.0"
    assert result["runtime"]["dlubal.api"] == "2.14.13"
