"""V3 metadata startup uses the shared Connector control channel."""

import json
from io import BytesIO

import pytest

from rhino_mcp.build_identity import CONNECTOR_BUILD_ID
from rhino_mcp.managed_bootstrap import read_connector_envelope
from tests.fixtures.bridge_contract import CONTRACT_DIGEST


def metadata_payload():
    from tests.connector.test_managed_bootstrap import _payload

    value = _payload()
    value["mode"] = "metadata"
    return value


def parse(payload):
    return read_connector_envelope(BytesIO(payload))



def test_metadata_startup_accepts_no_bridge_or_local_path():
    envelope = parse(json.dumps(metadata_payload()).encode())
    assert envelope.mode == "metadata"
    assert envelope.nativeContext is None
    assert envelope.binding["packageId"] == "rhino"


@pytest.mark.asyncio
async def test_no_rhino_or_bridge_metadata_is_discoverable():
    from rhino_mcp.server import create_mcp_server
    from tests.fixtures.mcp_client import connected_client

    async with connected_client(create_mcp_server()) as client:
        assert len((await client.list_tools()).tools) == 70
        assert len((await client.list_resources()).resources) == 1
        assert len((await client.list_resource_templates()).resource_templates) == 3
        assert len((await client.list_prompts()).prompts) == 2
        result = await client.call_tool("delete_object", {"all": True})
        assert result.is_error


@pytest.mark.asyncio
async def test_late_bridge_connection_preserves_frozen_contract():
    from rhino_mcp.bridge import BridgeClient
    from rhino_mcp.server import create_mcp_server
    from tests.fixtures.bridge_contract import FakeBridge, bridge_config
    from tests.fixtures.mcp_client import connected_client

    def respond(command):
        if command["type"] == "describe_capabilities":
            return {
                "status": "success",
                "result": {
                    "version": "0.1.2",
                    "command_count": 2,
                    "commands": [
                        {"name": name, "read_only": True}
                        for name in ("describe_capabilities", "get_document_summary")
                    ],
                    "perception": {"description": "fixture", "envelope_flags": []},
                },
            }
        return {
            "status": "success",
            "result": {
                "meta_data": {"name": None, "tolerance": 0.001, "units": "Meters"},
                "object_count": 0,
                "objects_by_type": {},
                "objects_by_layer": {},
                "layer_count": 0,
                "layer_hierarchy": [],
            },
        }

    with FakeBridge(response=respond) as fake:
        bridge = BridgeClient(bridge_config(fake.endpoint))
        async with connected_client(create_mcp_server(bridge)) as client:
            before = [tool.model_dump() for tool in (await client.list_tools()).tools]
            assert fake.connection_count == 0
            result = await client.call_tool("get_document_summary", {})
            assert not result.is_error
            after = [tool.model_dump() for tool in (await client.list_tools()).tools]
            assert after == before
            assert fake.connection_count == 1
        assert not bridge.is_connected


@pytest.mark.asyncio
async def test_managed_metadata_never_constructs_bridge(monkeypatch):
    from rhino_mcp import managed_bootstrap
    from tests.fixtures.mcp_client import connected_client

    def forbidden(*args, **kwargs):
        raise AssertionError("metadata constructed a native Bridge transport")

    monkeypatch.setattr(managed_bootstrap, "BridgeClient", forbidden)
    envelope = parse(json.dumps(metadata_payload()).encode())
    server, bridge = managed_bootstrap.create_managed_server(envelope)
    assert bridge is None
    async with connected_client(server) as client:
        assert len((await client.list_tools()).tools) == 70
        assert (await client.call_tool("get_document_summary", {})).is_error


@pytest.mark.asyncio
async def test_typed_execution_context_authenticates_actual_fixture():
    from rhino_mcp.managed_bootstrap import create_managed_server
    from tests.fixtures.bridge_contract import FakeBridge
    from tests.fixtures.mcp_client import connected_client
    from tests.connector.test_managed_bootstrap import _payload

    def respond(command):
        return {
            "status": "success",
            "result": {
                "version": "0.1.2",
                "command_count": 1,
                "commands": [{"name": "describe_capabilities", "read_only": True}],
                "perception": {"description": "fixture", "envelope_flags": []},
            },
        }

    with FakeBridge(response=respond) as fake:
        payload = _payload()
        payload["binding"]["generation"] = 2
        payload["nativePeer"] = {
            "host": "127.0.0.1",
            "port": fake.endpoint[1],
            "token": "test-bridge-token-0123456789abcdef",
            "generation": 1,
            "connectorBuildId": CONNECTOR_BUILD_ID,
            "contractDigest": CONTRACT_DIGEST,
            "softwareInstanceId": "si_fixture",
        }
        envelope = parse(json.dumps(payload).encode())
        server, bridge = create_managed_server(envelope)
        async with connected_client(server) as client:
            assert fake.connection_count == 0
            result = await client.call_tool("describe_capabilities", {})
            assert not result.is_error
            assert bridge.lifecycle_snapshot() == ("idle", 0)
        ready = {"schemaVersion":3,"kind":"ready","binding":envelope.binding,"connectorBuildId":envelope.connectorBuildId,"port":envelope.bind.port}
        assert ready["binding"]["generation"] == 2
        auth = fake.authentication_frames[0]
        assert auth["generation"] == 1
        assert auth["token"] == "test-bridge-token-0123456789abcdef"
        assert auth["token"] != payload["bearerToken"]
        assert auth["connectorBuildId"] == payload["connectorBuildId"]
        assert auth["contractDigest"] == payload["nativePeer"]["contractDigest"]


@pytest.mark.asyncio
async def test_contextless_tool_is_rejected_in_real_metadata_request():
    from rhino_mcp.server import create_mcp_server
    from tests.fixtures.mcp_client import connected_client

    async with connected_client(create_mcp_server()) as client:
        result = await client.call_tool("get_modeling_guidance", {})
        assert result.is_error
        assert "metadata" in str(result.content)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "native_generation,token",
    [
        (2, "test-bridge-token-0123456789abcdef"),
        (1, "expired-bridge-token-0123456789abcdef"),
    ],
)
async def test_untrusted_bridge_lease_rejected_before_engineering_dispatch(
    native_generation, token
):
    from rhino_mcp.managed_bootstrap import create_managed_server
    from tests.fixtures.bridge_contract import FakeBridge
    from tests.fixtures.mcp_client import connected_client
    from tests.connector.test_managed_bootstrap import _payload

    with FakeBridge() as fake:
        payload = _payload()
        payload["binding"]["generation"] = 2
        payload["nativePeer"] = {
            "host": "127.0.0.1",
            "port": fake.endpoint[1],
            "token": token,
            "generation": native_generation,
            "connectorBuildId": CONNECTOR_BUILD_ID,
            "contractDigest": CONTRACT_DIGEST,
            "softwareInstanceId": "si_fixture",
        }
        envelope = parse(json.dumps(payload).encode())
        server, bridge = create_managed_server(envelope)
        async with connected_client(server) as client:
            result = await client.call_tool("describe_capabilities", {})
            assert result.is_error
            assert "rejected" in str(result.content)
            assert bridge.lifecycle_snapshot() == ("idle", 0)
        assert fake.command_frames == []
        assert len(fake.authentication_frames) == 1
        assert fake.authentication_frames[0]["generation"] == native_generation
