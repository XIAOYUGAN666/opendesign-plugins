from rhino_mcp.setup import diagnose_bridge


def test_setup_reports_unloaded_bridge():
    assert diagnose_bridge(rhino_running=False, bridge_ready=False)["reason"] == "softwareNotRunning"
    assert diagnose_bridge(rhino_running=True, bridge_ready=False)["reason"] == "bridgeNotLoaded"
    assert diagnose_bridge(rhino_running=True, bridge_ready=True)["type"] == "completed"
