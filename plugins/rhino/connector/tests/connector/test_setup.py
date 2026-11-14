from contextlib import contextmanager

from rhino_mcp import _connector_process, stdio_bridge
from rhino_mcp.setup import diagnose_bridge, diagnose_connection


def test_setup_reports_unloaded_bridge():
    assert diagnose_bridge(rhino_running=False, bridge_ready=False)["reason"] == "softwareNotRunning"
    assert diagnose_bridge(rhino_running=True, bridge_ready=False)["reason"] == "bridgeNotLoaded"
    assert diagnose_bridge(rhino_running=True, bridge_ready=True)["type"] == "completed"


def test_setup_checks_the_existing_bridge_without_dispatching_a_command(monkeypatch):
    calls = []

    @contextmanager
    def bridge():
        calls.append("handshake")
        yield object()
        calls.append("cleanup")

    monkeypatch.setattr(_connector_process, "running_instance", lambda _: object())
    monkeypatch.setattr(stdio_bridge, "await_bridge", bridge)
    assert diagnose_connection()["type"] == "completed"
    assert calls == ["handshake", "cleanup"]
