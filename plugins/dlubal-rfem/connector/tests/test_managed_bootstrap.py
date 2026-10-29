"""The Connector process accepts only the v3 duplex control channel."""

import asyncio
import io
import json
import os
import socket
import sys

import pytest

from dlubal_rfem_mcp.core.identity import connector_build_id
from dlubal_rfem_mcp.managed_bootstrap import BootstrapError, read_connector_envelope


def _runtime_envelope(tmp_path):
    return {
        "schemaVersion": 3,
        "binding": {
            "packageId": "dlubal-rfem",
            "releaseDigest": "sha256:" + "a" * 64,
            "connectorInstanceId": "ci_" + "b" * 32,
            "generation": 1,
            "configurationRevision": 1,
            "secretRevision": 1,
        },
        "mode": "runtime",
        "bind": {"host": "127.0.0.1", "port": 8765, "path": "/mcp"},
        "bearerToken": "b" * 40,
        "connectorBuildId": connector_build_id(),
        "configuration": {},
        "secrets": [{"fieldId": "dlubal-api-key", "value": "fixture-manual"}],
        "dataDirectory": str(tmp_path),
        "nativePeer": None,
    }


def test_startup_frame_leaves_following_control_frame_unread(tmp_path):
    value = _runtime_envelope(tmp_path)
    following = b'{"schemaVersion":1,"kind":"ensureSoftwareConnection"}\n'
    stream = io.BytesIO(json.dumps(value).encode() + b"\n" + following)
    assert read_connector_envelope(stream).generation == 1
    assert stream.read() == following


def test_v3_runtime_starts_without_software_or_api_key(tmp_path):
    value = _runtime_envelope(tmp_path)
    value["secrets"] = []
    context = read_connector_envelope(io.BytesIO(json.dumps(value).encode() + b"\n"))
    assert context.configuration is None
    assert context.software is None
    assert context.binding == value["binding"]


@pytest.mark.asyncio
async def test_v3_process_accepts_connection_control_without_credential(monkeypatch, tmp_path):
    from dlubal_rfem_mcp import managed_bootstrap
    from dlubal_rfem_mcp.core import identity

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    listener.close()
    value = _runtime_envelope(tmp_path)
    value["bind"]["port"] = port
    value["secrets"] = []
    context = read_connector_envelope(io.BytesIO(json.dumps(value).encode() + b"\n"))
    read_fd, write_fd = os.pipe()
    reader = os.fdopen(read_fd, "r", encoding="utf-8")
    writer = os.fdopen(write_fd, "wb", buffering=0)
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdin", reader)
    monkeypatch.setattr(managed_bootstrap, "verify_runtime", lambda: None)
    monkeypatch.setattr(identity, "verify_connector_build", lambda _value: None)
    task = asyncio.create_task(managed_bootstrap._serve_owned(context, output))
    try:
        async with asyncio.timeout(5):
            while '"kind":"ready"' not in output.getvalue():
                await asyncio.sleep(0.01)
            command = {"schemaVersion":1,"kind":"ensureSoftwareConnection","operationId":"c5ee5294-a247-4eea-a4a8-a1413d7db05a","cause":"manual","binding":value["binding"],"deadlineMs":1000}
            writer.write(json.dumps(command).encode() + b"\n")
            while '"kind":"connectionCheckCompleted"' not in output.getvalue():
                await asyncio.sleep(0.01)
        events = [json.loads(line) for line in output.getvalue().splitlines()]
        completed = next(event for event in events if event["kind"] == "connectionCheckCompleted")
        assert completed["outcome"] == "blocked"
        assert completed["reason"] == "credentialMissing"
    finally:
        writer.close()
        await asyncio.wait_for(task, timeout=5)
        reader.close()


@pytest.mark.parametrize("schema_version", [1, 2, 4])
def test_legacy_or_unknown_startup_versions_are_rejected(tmp_path, schema_version):
    value = _runtime_envelope(tmp_path)
    value["schemaVersion"] = schema_version
    with pytest.raises(BootstrapError):
        read_connector_envelope(io.BytesIO(json.dumps(value).encode()))


def test_v3_bootstrap_rejects_unknown_fields_duplicates_and_bad_secret_slots(tmp_path):
    value = _runtime_envelope(tmp_path)
    value["oldConnectionState"] = "connected"
    with pytest.raises(BootstrapError):
        read_connector_envelope(io.BytesIO(json.dumps(value).encode()))
    value = _runtime_envelope(tmp_path)
    value["secrets"] = [{"fieldId": "other", "value": "secret"}]
    with pytest.raises(BootstrapError):
        read_connector_envelope(io.BytesIO(json.dumps(value).encode()))
    raw = json.dumps(_runtime_envelope(tmp_path), separators=(",", ":"))
    raw = raw.replace('"schemaVersion":3', '"schemaVersion":3,"schemaVersion":3')
    with pytest.raises(BootstrapError):
        read_connector_envelope(io.BytesIO(raw.encode()))
