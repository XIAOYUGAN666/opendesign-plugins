"""The Connector receives its authenticated Bridge dependency through lifespan."""

from __future__ import annotations

import pytest

from rhino_mcp.bridge import BridgeClient, BridgeResultUnknown, BridgeUnavailable
from tests.fixtures.bridge_contract import bridge_config
from tests.fixtures.mcp_client import connected_client
from tests.fixtures.mock_rhino_server import MockRhinoServer


def test_provider_does_not_discover_rhino_endpoint_from_environment(
    monkeypatch,
) -> None:
    from rhino_mcp.server import get_rhino_connection

    monkeypatch.setenv("RHINO_" + "MCP_" + "HOST", "192.0.2.20")
    monkeypatch.setenv("RHINO_" + "MCP_" + "PORT", "54321")
    monkeypatch.setenv("RHINO_" + "MCP_" + "TIMEOUT", "1")
    with pytest.raises(BridgeUnavailable) as caught:
        get_rhino_connection()
    assert "127.0.0.1" not in str(caught.value)
    assert "192.0.2.20" not in str(caught.value)


@pytest.mark.asyncio
async def test_server_lifespan_injects_one_authenticated_bridge_into_tool_calls() -> (
    None
):
    from rhino_mcp.server import create_mcp_server

    mock = MockRhinoServer()
    mock.start()
    bridge = BridgeClient(mock.provider_config())
    try:
        server = create_mcp_server(bridge)
        async with connected_client(server) as client:
            result = await client.call_tool("get_document_summary", {})
            assert result.is_error is not True
        assert mock.received_commands.count("get_document_summary") == 1
        assert mock.received_commands.count("describe_capabilities") == 1
        assert mock.received_authentications[0]["type"] == "authenticate"
        assert bridge.is_connected is False
    finally:
        bridge.disconnect()
        mock.stop()


@pytest.mark.asyncio
async def test_agent_can_use_bridge_received_after_server_start() -> None:
    from rhino_mcp.server import create_mcp_server

    mock = MockRhinoServer()
    mock.start()
    bridge = BridgeClient(mock.provider_config())
    server = create_mcp_server()
    try:
        async with connected_client(server) as client:
            server.connector_bridge_slot.update(bridge)
            result = await client.call_tool("get_document_summary", {})
            assert result.is_error is not True
            assert "get_document_summary" in mock.received_commands
    finally:
        bridge.disconnect()
        mock.stop()


def test_new_bridge_does_not_clear_unknown_engineering_result() -> None:
    from rhino_mcp.server import create_mcp_server
    from tests.fixtures.bridge_contract import FakeBridge

    with FakeBridge() as fake:
        first = BridgeClient(bridge_config(fake.endpoint))
        first._result_unknown = True
        next_bridge = BridgeClient(bridge_config(fake.endpoint))
        server = create_mcp_server(first)
        server.connector_bridge_slot.update(next_bridge)
        assert server.connector_bridge_slot.bridge_client is first
        assert first.lifecycle_snapshot()[0] == "unknown"


def test_peer_update_does_not_interrupt_in_flight_engineering_call() -> None:
    import threading
    from unittest.mock import patch

    from rhino_mcp.server import RhinoConnection, create_mcp_server
    from tests.fixtures.bridge_contract import FakeBridge

    with FakeBridge() as fake:
        first = BridgeClient(bridge_config(fake.endpoint))
        second = BridgeClient(bridge_config(fake.endpoint))
        slot = create_mcp_server(first).connector_bridge_slot
        started, finish = threading.Event(), threading.Event()

        def work(_self, _command, _params=None):
            started.set()
            assert finish.wait(2)
            return "done"

        result = []
        with patch.object(RhinoConnection, "send_command", work):
            worker = threading.Thread(target=lambda: result.append(slot.send_command("test")))
            worker.start()
            assert started.wait(1)
            slot.update(second)
            assert slot.bridge_client is first
            finish.set()
            worker.join(timeout=2)
            assert result == ["done"]
            assert slot.send_command("test") == "done"
            assert slot.bridge_client is second


def test_bridge_client_does_not_replay_a_dispatched_command() -> None:
    from rhino_mcp.server import RhinoConnection
    from tests.fixtures.bridge_contract import FakeBridge

    with FakeBridge(close_after_command=True) as fake:
        bridge = BridgeClient(bridge_config(fake.endpoint))
        connection = RhinoConnection(bridge)
        connection.connect()

        with pytest.raises(BridgeResultUnknown) as caught:
            connection.send_command("describe_capabilities", {})

        assert caught.value.category == "resultUnknown"
        assert [frame["type"] for frame in fake.command_frames] == [
            "describe_capabilities"
        ]
        assert fake.connection_count == 1
        connection.disconnect()


def test_connection_probe_authenticates_and_does_not_dispatch_engineering_commands() -> None:
    from tests.fixtures.bridge_contract import FakeBridge

    with FakeBridge() as fake:
        bridge = BridgeClient(bridge_config(fake.endpoint))
        try:
            bridge.confirm_connection(1.0)
            bridge.confirm_connection(1.0)
            assert fake.connection_count == 1
            assert len(fake.connection_probes) == 2
            assert fake.command_frames == []
        finally:
            bridge.disconnect()


def test_wrong_probe_response_cannot_be_reported_as_connected() -> None:
    from tests.fixtures.bridge_contract import FakeBridge

    with FakeBridge(probe_response=lambda _request: {"type": "connectionProbeAck", "nonce": "stale"}) as fake:
        bridge = BridgeClient(bridge_config(fake.endpoint))
        with pytest.raises(BridgeUnavailable):
            bridge.confirm_connection(1.0)
        assert not bridge.is_connected
        assert fake.command_frames == []


def test_connection_probe_keeps_unknown_engineering_result_intact() -> None:
    from tests.fixtures.bridge_contract import FakeBridge

    with FakeBridge() as fake:
        bridge = BridgeClient(bridge_config(fake.endpoint))
        try:
            bridge._result_unknown = True
            bridge.confirm_connection(1.0)
            assert bridge.lifecycle_snapshot()[0] == "unknown"
            assert fake.command_frames == []
        finally:
            bridge.disconnect()


def test_startup_recovery_message_does_not_disclose_endpoint() -> None:
    from rhino_mcp.server import rhino_startup_error_message

    message = rhino_startup_error_message()
    assert "Open Rhino" in message
    assert "plugin manager" in message
    assert "127.0.0.1" not in message
    assert ":" not in message


def test_socket_creation_failure_is_normalized_before_dispatch() -> None:
    from unittest.mock import patch

    with (
        patch("rhino_mcp.bridge.socket.socket", side_effect=OSError("socket denied")),
        pytest.raises(BridgeUnavailable) as caught,
    ):
        BridgeClient(bridge_config(("127.0.0.1", 49999))).connect()

    assert "socket denied" not in str(caught.value)
