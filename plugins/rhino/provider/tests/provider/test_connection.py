"""The Provider receives its authenticated Bridge dependency through lifespan."""

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
