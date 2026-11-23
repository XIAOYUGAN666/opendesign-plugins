from datetime import datetime, timedelta, timezone
import json
import os

import pytest

from rhino_mcp import instances
from rhino_mcp.bridge import BridgeUnavailable


def test_selects_the_requested_port_independently_of_process_order(monkeypatch):
    monkeypatch.setattr(instances, "instances", lambda: [
        ("two", 12, {"port": 2000}),
        ("one", 11, {"port": 1999}),
        ("unloaded", 13, {}),
    ])
    assert instances.choose_target() == (11, "rhino-mcp-control-v1-11")
    assert instances.choose_target(2000) == (12, "rhino-mcp-control-v1-12")
    with pytest.raises(BridgeUnavailable):
        instances.choose_target(2001)


def test_stale_status_and_reused_process_ids_cannot_resolve_a_listener(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    directory = tmp_path / "RhinoMcpBridge" / "instances"
    directory.mkdir(parents=True)
    file = directory / "11.json"
    now = datetime.now(timezone.utc)
    status = {"pid": 11, "created": "12345678", "updatedAt": now.isoformat(), "port": 1999}
    file.write_text(json.dumps(status), encoding="utf-8")
    assert instances._read_instance(11, 12345678)["port"] == 1999
    assert instances._read_instance(11, 12345679) == {}
    status["updatedAt"] = (now - timedelta(seconds=15)).isoformat()
    file.write_text(json.dumps(status), encoding="utf-8")
    assert instances._read_instance(11, 12345678) == {}


@pytest.mark.skipif(os.name != "nt", reason="Windows known folder")
def test_minimal_windows_environment_resolves_the_native_status_directory(monkeypatch):
    expected = instances._local_app_data()
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    assert instances._local_app_data().resolve() == expected.resolve()
