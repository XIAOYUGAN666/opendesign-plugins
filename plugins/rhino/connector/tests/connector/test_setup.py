from contextlib import contextmanager

from rhino_mcp import instances, stdio_bridge
from rhino_mcp.setup import diagnose_bridge, diagnose_connection


def test_setup_reports_unloaded_bridge():
    assert diagnose_bridge(rhino_running=False, bridge_ready=False)["reason"] == "softwareNotRunning"
    assert diagnose_bridge(rhino_running=True, bridge_ready=False)["reason"] == "bridgeNotLoaded"
    assert diagnose_bridge(rhino_running=True, bridge_ready=True)["type"] == "completed"


def test_setup_checks_the_existing_bridge_without_dispatching_a_command(monkeypatch):
    calls = []

    @contextmanager
    def bridge(target, **settings):
        assert target.port == 1999
        calls.append("handshake")
        yield object()
        calls.append("cleanup")

    monkeypatch.delenv("PLUGIN_DATA", raising=False)
    monkeypatch.setattr(instances, "instances", lambda: [("test", 1, {"port": 1999})])
    monkeypatch.setattr(stdio_bridge, "await_bridge", bridge)
    assert diagnose_connection()["type"] == "completed"
    assert calls == ["handshake", "cleanup"]
