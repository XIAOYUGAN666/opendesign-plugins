import pytest
from pathlib import Path
from scripts.build_fixture import build_fixture


def test_builder_outputs_real_embedded_interpreter_and_frozen_inventory(tmp_path):
    import json

    root = Path(__file__).resolve().parents[2]
    staged = build_fixture(root, tmp_path / "build")
    assert (staged / "runtime/python/python.exe").is_file()
    locked = json.loads((staged / "runtime-lock.json").read_text())
    assert locked["python"]["filename"] == "python-3.12.10-embed-amd64.zip"
    assert {x["name"]: x["version"] for x in locked["wheels"]}["fastmcp"] == "4.0.3"
    assert (staged / "startup-envelope.schema.json").is_file()
    assert (
        json.loads((staged / "plugin.json").read_text())["name"]
        == "fixture-engineering"
    )
