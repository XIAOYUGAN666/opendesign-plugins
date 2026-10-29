"""The Connector process uses one bounded v3 duplex startup/control channel."""

import asyncio
import json
import os
import socket
import sys
from io import BytesIO, StringIO

import pytest

from rhino_mcp.build_identity import CONNECTOR_BUILD_ID
from rhino_mcp.managed_bootstrap import (
    ConfigurationV1,
    MAX_STARTUP_BYTES,
    StartupConfigurationError,
    read_connector_envelope,
)


def _payload():
    return {
        "schemaVersion": 3,
        "binding": {
            "packageId": "rhino",
            "releaseDigest": "sha256:" + "a" * 64,
            "connectorInstanceId": "ci_0123456789abcdef0123456789abcdef",
            "generation": 1,
            "configurationRevision": 1,
            "secretRevision": 0,
        },
        "mode": "runtime",
        "bind": {"host": "127.0.0.1", "port": 49152, "path": "/mcp"},
        "bearerToken": "test-connector-token-0123456789abcdef",
        "connectorBuildId": CONNECTOR_BUILD_ID,
        "configuration": {},
        "secrets": [],
        "dataDirectory": None,
        "nativePeer": None,
    }


def test_rhino_configuration_is_typed_and_has_no_user_fields():
    assert ConfigurationV1.model_json_schema() == {
        "additionalProperties": False,
        "description": "Rhino 插件无需 API Key。请启动兼容版本的 Rhino 8 以建立连接。",
        "properties": {},
        "title": "Rhino",
        "type": "object",
    }
    with pytest.raises(ValueError):
        ConfigurationV1.model_validate({"apiKey": "not-accepted"})


def test_startup_frame_leaves_following_control_frame_unread():
    following = b'{"schemaVersion":1,"kind":"ensureSoftwareConnection"}\n'
    stream = BytesIO(json.dumps(_payload()).encode() + b"\n" + following)
    assert read_connector_envelope(stream).generation == 1
    assert stream.read() == following


def test_connector_starts_before_rhino_and_waits_for_authenticated_native_peer():
    context = read_connector_envelope(BytesIO(json.dumps(_payload()).encode() + b"\n"))
    assert context.mode == "execution"
    assert context.nativeContext is None
    assert context.binding["connectorInstanceId"] == "ci_0123456789abcdef0123456789abcdef"


@pytest.mark.parametrize("version", [1, 2, 4])
def test_legacy_or_unknown_startup_contract_is_rejected(version):
    payload = _payload()
    payload["schemaVersion"] = version
    with pytest.raises(StartupConfigurationError):
        read_connector_envelope(BytesIO(json.dumps(payload).encode()))


def test_v3_rejects_unknown_fields_duplicate_members_and_oversize():
    payload = _payload()
    payload["oldConnectionState"] = "connected"
    with pytest.raises(StartupConfigurationError):
        read_connector_envelope(BytesIO(json.dumps(payload).encode()))
    encoded = json.dumps(_payload(), separators=(",", ":"))
    duplicate = encoded.replace('"schemaVersion":3', '"schemaVersion":3,"schemaVersion":3')
    with pytest.raises(StartupConfigurationError):
        read_connector_envelope(BytesIO(duplicate.encode()))
    oversized = encoded.encode() + b" " * (MAX_STARTUP_BYTES + 1)
    with pytest.raises(StartupConfigurationError):
        read_connector_envelope(BytesIO(oversized))


@pytest.mark.asyncio
async def test_v3_connection_control_connects_after_native_peer_arrives(monkeypatch):
    from rhino_mcp import managed_bootstrap, software_connection
    from tests.fixtures.bridge_contract import CONTRACT_DIGEST, TOKEN, FakeBridge

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    listener.close()
    payload = _payload()
    payload["bind"]["port"] = port
    context = read_connector_envelope(BytesIO(json.dumps(payload).encode() + b"\n"))
    read_fd, write_fd = os.pipe()
    reader = os.fdopen(read_fd, "r", encoding="utf-8")
    writer = os.fdopen(write_fd, "wb", buffering=0)
    output = StringIO()
    monkeypatch.setattr(sys, "stdin", reader)
    monkeypatch.setattr(sys, "stdout", output)
    monkeypatch.setattr(software_connection, "running_instance", lambda _name: "si_fixture")
    task = asyncio.create_task(managed_bootstrap._serve(context))
    try:
        async with asyncio.timeout(5):
            while '"kind":"ready"' not in output.getvalue():
                await asyncio.sleep(0.01)
            command = {"schemaVersion":1,"kind":"ensureSoftwareConnection","operationId":"c5ee5294-a247-4eea-a4a8-a1413d7db05a","cause":"manual","binding":payload["binding"],"deadlineMs":1000}
            writer.write(json.dumps(command).encode() + b"\n")
            while '"kind":"connectionCheckCompleted"' not in output.getvalue():
                await asyncio.sleep(0.01)
        completed = next(event for event in map(json.loads, output.getvalue().splitlines()) if event["kind"] == "connectionCheckCompleted")
        assert completed["outcome"] == "blocked"
        assert completed["reason"] == "nativeComponentUnavailable"
        with FakeBridge() as fake:
            peer = {"host":"127.0.0.1","port":fake.endpoint[1],"token":TOKEN,"generation":1,"connectorBuildId":CONNECTOR_BUILD_ID,"contractDigest":CONTRACT_DIGEST,"softwareInstanceId":"si_fixture"}
            writer.write(json.dumps({"schemaVersion":1,"kind":"nativePeerChanged","binding":payload["binding"],"peerRevision":1,"peer":peer}).encode() + b"\n")
            await asyncio.sleep(0.05)
            command["operationId"] = "c5ee5294-a247-4eea-a4a8-a1413d7db05b"
            writer.write(json.dumps(command).encode() + b"\n")
            async with asyncio.timeout(5):
                while len([event for event in map(json.loads, output.getvalue().splitlines()) if event["kind"] == "connectionCheckCompleted"]) < 2:
                    await asyncio.sleep(0.01)
            completed = [event for event in map(json.loads, output.getvalue().splitlines()) if event["kind"] == "connectionCheckCompleted"][-1]
            assert completed["outcome"] == "confirmed"
            assert completed["softwareInstanceId"] == "si_fixture"
            assert fake.command_frames == []
    finally:
        writer.close()
        await asyncio.wait_for(task, timeout=5)
        reader.close()
