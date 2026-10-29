"""A lost post-dispatch response is never replayed by the Connector."""

from __future__ import annotations

import time

import pytest

from rhino_mcp.bridge import BridgeClient, BridgeResultUnknown
from tests.fixtures.bridge_contract import FakeBridge, bridge_config


def test_read_only_command_with_lost_response_is_result_unknown_and_not_retried() -> (
    None
):
    with FakeBridge(close_after_command=True) as fake:
        client = BridgeClient(bridge_config(fake.endpoint))

        with pytest.raises(BridgeResultUnknown) as caught:
            client.call("get_document_summary", {})

        time.sleep(0.05)
        assert caught.value.category == "resultUnknown"
        assert len(fake.command_frames) == 1
        assert fake.command_frames[0]["type"] == "get_document_summary"
        assert fake.connection_count == 1


def test_unknown_result_blocks_every_later_dispatch_after_disconnect():
    with FakeBridge(close_after_command=True) as fake:
        client = BridgeClient(bridge_config(fake.endpoint))
        with pytest.raises(BridgeResultUnknown):
            client.call("delete_object", {"all": True})
        client.disconnect()
        with pytest.raises(BridgeResultUnknown):
            client.call("get_document_summary", {})
        assert len(fake.command_frames) == 1
        assert fake.connection_count == 1


def test_invalid_post_dispatch_response_blocks_next_dispatch():
    with FakeBridge(
        response=lambda command: {"status": "success", "result": {}}
    ) as fake:
        client = BridgeClient(bridge_config(fake.endpoint))
        with pytest.raises(BridgeResultUnknown):
            client.call("get_document_summary", {})
        with pytest.raises(BridgeResultUnknown):
            client.call("delete_object", {"all": True})
        assert len(fake.command_frames) == 1


@pytest.mark.asyncio
async def test_mcp_unknown_write_is_error_and_stays_unknown_without_replay():
    from rhino_mcp.server import create_mcp_server
    from tests.fixtures.mcp_client import connected_client

    def respond(command):
        if command["type"] == "describe_capabilities":
            return {
                "status": "success",
                "result": {
                    "version": "0.1.2",
                    "command_count": 2,
                    "commands": [
                        {
                            "name": "describe_capabilities",
                            "read_only": True,
                            "supports_dry_run": False,
                        },
                        {
                            "name": "delete_object",
                            "read_only": False,
                            "supports_dry_run": False,
                        },
                    ],
                    "perception": {"description": "fixture", "envelope_flags": []},
                },
            }
        # A syntactically valid response whose delete result is unverifiable.
        return {"status": "success", "result": {}}

    with FakeBridge(response=respond) as fake:
        bridge = BridgeClient(bridge_config(fake.endpoint))
        async with connected_client(create_mcp_server(bridge)) as client:
            first = await client.call_tool("delete_object", {"all": True})
            assert first.is_error
            assert "Do not retry automatically" in str(first.content)
            assert bridge.lifecycle_snapshot() == ("unknown", 0)
            bridge.disconnect()
            second = await client.call_tool("get_document_summary", {})
            assert second.is_error
            assert bridge.lifecycle_snapshot() == ("unknown", 0)
        assert [frame["type"] for frame in fake.command_frames] == [
            "describe_capabilities",
            "delete_object",
        ]
        assert fake.connection_count == 1


def test_real_transport_lifecycle_stays_running_until_response():
    import threading
    from concurrent.futures import ThreadPoolExecutor

    entered, release = threading.Event(), threading.Event()

    def respond(command):
        entered.set()
        assert release.wait(3)
        return {
            "status": "success",
            "result": {"deleted": True, "count": 0, "scope": "all"},
        }

    with FakeBridge(response=respond) as fake:
        bridge = BridgeClient(bridge_config(fake.endpoint))
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(bridge.call, "delete_object", {"all": True})
            try:
                assert entered.wait(1)
                assert bridge.lifecycle_snapshot() == ("running", 1)
                assert not future.done()
            finally:
                release.set()
            assert future.result(timeout=2)["deleted"]
        assert bridge.lifecycle_snapshot() == ("idle", 0)
        bridge.disconnect()


def test_predispatch_connection_failure_is_known_idle():
    from rhino_mcp.bridge import BridgeUnavailable
    import socket

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        endpoint = listener.getsockname()
    bridge = BridgeClient(bridge_config(endpoint))
    with pytest.raises(BridgeUnavailable):
        bridge.call("delete_object", {"all": True})
    assert bridge.lifecycle_snapshot() == ("idle", 0)


def test_unexpected_response_decode_failure_is_sticky_unknown(monkeypatch):
    import rhino_mcp.bridge as transport

    with FakeBridge() as fake:
        client = BridgeClient(bridge_config(fake.endpoint))
        client.connect()

        def decode_failure(sock):
            raise RecursionError("untrusted deeply nested response")

        monkeypatch.setattr(transport, "read_frame", decode_failure)
        with pytest.raises(BridgeResultUnknown):
            client.call("delete_object", {"all": True})
        assert client.lifecycle_snapshot() == ("unknown", 0)
        with pytest.raises(BridgeResultUnknown):
            client.call("delete_object", {"all": True})


def test_wellformed_rhino_rejection_remains_known_idle():
    from rhino_mcp.bridge import RhinoCommandError

    with FakeBridge(
        response=lambda command: {"status": "error", "message": "Command rejected."}
    ) as fake:
        client = BridgeClient(bridge_config(fake.endpoint))
        with pytest.raises(RhinoCommandError):
            client.call("delete_object", {"all": True})
        assert client.lifecycle_snapshot() == ("idle", 0)
        client.disconnect()


@pytest.mark.asyncio
async def test_contextless_tool_cannot_bypass_sticky_unknown():
    from rhino_mcp.server import create_mcp_server
    from tests.fixtures.mcp_client import connected_client

    with FakeBridge(close_after_command=True) as fake:
        bridge = BridgeClient(bridge_config(fake.endpoint))
        with pytest.raises(BridgeResultUnknown):
            bridge.call("delete_object", {"all": True})
        async with connected_client(create_mcp_server(bridge)) as client:
            result = await client.call_tool("get_modeling_guidance", {})
            assert result.is_error
            assert bridge.lifecycle_snapshot() == ("unknown", 0)
        assert [frame["type"] for frame in fake.command_frames] == ["delete_object"]
        assert fake.connection_count == 1
