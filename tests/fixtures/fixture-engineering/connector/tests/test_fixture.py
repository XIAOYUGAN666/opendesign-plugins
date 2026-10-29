from pathlib import Path

import pytest
from fastmcp import Client

from fixture_engineering.managed_bootstrap import build_mcp


def binding():
    return {
        "packageId": "fixture-engineering",
        "releaseDigest": "sha256:" + "a" * 64,
        "connectorInstanceId": "ci_" + "1" * 32,
        "generation": 1,
        "configurationRevision": 1,
        "secretRevision": 0,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("requested,expected", [(None, "unknown"), ("idle", "idle")])
async def test_v3_runtime_reports_typed_execution_state(tmp_path, requested, expected):
    import asyncio
    import json
    import socket
    import sys

    from fixture_engineering.managed_bootstrap import ConfigurationV1, connector_build_id

    assert "lifecycleTestState" in ConfigurationV1.model_fields
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    identity = binding()
    envelope = {
        "schemaVersion": 3,
        "mode": "runtime",
        "binding": identity,
        "bind": {"host": "127.0.0.1", "port": port, "path": "/mcp"},
        "bearerToken": "b" * 64,
        "connectorBuildId": connector_build_id(Path(__file__).resolve().parents[1] / "fixture_engineering"),
        "configuration": {},
        "secrets": [],
        "dataDirectory": str(tmp_path),
        "nativePeer": None,
    }
    if requested:
        envelope["configuration"] = {"lifecycleTestState": requested}
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-I",
        "-s",
        "-B",
        "-m",
        "fixture_engineering.managed_bootstrap",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        process.stdin.write(json.dumps(envelope).encode() + b"\n")
        await process.stdin.drain()
        ready = json.loads(await asyncio.wait_for(process.stdout.readline(), 20))
        assert ready == {
            "schemaVersion": 3,
            "kind": "ready",
            "binding": identity,
            "connectorBuildId": envelope["connectorBuildId"],
            "port": port,
        }
        frame = json.loads(await asyncio.wait_for(process.stdout.readline(), 2))
        assert frame == {
            "schemaVersion": 1,
            "kind": "status",
            "connectorInstanceId": identity["connectorInstanceId"],
            "generation": 1,
            "sequence": 1,
            "state": expected,
            "inFlight": 0,
        }
    finally:
        if process.returncode is None:
            process.terminate()
        await asyncio.wait_for(process.wait(), 10)


@pytest.mark.asyncio
async def test_fixture_tools_are_real_mcp_declared(tmp_path):
    async with Client(build_mcp("ci_" + "1" * 32, tmp_path)) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}
        assert set(tools) == {"echo", "synthetic_write"}
        assert tools["echo"].input_schema["properties"]["value"]["type"] == "string"
        assert tools["echo"].annotations.read_only_hint is True
        assert tools["synthetic_write"].annotations.read_only_hint is False
        assert tools["synthetic_write"].annotations.destructive_hint is False
        result = await client.call_tool("echo", {"value": "hello"})
        assert result.data.value == "hello"
        assert result.data.connectorInstanceId == "ci_" + "1" * 32
        await client.call_tool("synthetic_write", {"value": "approved synthetic value"})
        assert (tmp_path / "synthetic-state.json").is_file()
        assert sorted(path.name for path in tmp_path.iterdir()) == ["synthetic-state.json"]
        with pytest.raises(Exception):
            await client.call_tool("synthetic_write", {"value": "x", "path": "../outside"})


@pytest.mark.asyncio
async def test_v3_metadata_startup_has_no_software_or_data_and_refuses_calls(tmp_path):
    import asyncio
    import json
    import socket
    import sys

    from fixture_engineering.managed_bootstrap import connector_build_id

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    identity = binding()
    envelope = {
        "schemaVersion": 3,
        "mode": "metadata",
        "binding": identity,
        "bind": {"host": "127.0.0.1", "port": port, "path": "/mcp"},
        "bearerToken": "a" * 64,
        "connectorBuildId": connector_build_id(Path(__file__).resolve().parents[1] / "fixture_engineering"),
        "configuration": {},
        "secrets": [],
        "dataDirectory": None,
        "nativePeer": None,
    }
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-I",
        "-s",
        "-B",
        "-m",
        "fixture_engineering.managed_bootstrap",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        process.stdin.write(json.dumps(envelope).encode() + b"\n")
        await process.stdin.drain()
        ready = json.loads(await asyncio.wait_for(process.stdout.readline(), 20))
        assert ready["kind"] == "ready"
        async with Client(build_mcp(identity["connectorInstanceId"], None, mode="metadata")) as client:
            assert {tool.name for tool in await client.list_tools()} == {"echo", "synthetic_write"}
            for name in ("echo", "synthetic_write"):
                with pytest.raises(Exception):
                    await client.call_tool(name, {"value": "must not execute"})
    finally:
        if process.returncode is None:
            process.terminate()
        await asyncio.wait_for(process.wait(), 10)
    assert not list(tmp_path.iterdir())


def test_only_v3_bootstrap_is_accepted_and_errors_hide_inputs():
    from fixture_engineering.managed_bootstrap import read_connector_envelope

    valid = {
        "schemaVersion": 3,
        "mode": "runtime",
        "binding": binding(),
        "bind": {"host": "127.0.0.1", "port": 8765, "path": "/mcp"},
        "bearerToken": "secret-canary-" + "a" * 40,
        "connectorBuildId": "sha256:" + "c" * 64,
        "configuration": {},
        "secrets": [],
        "dataDirectory": None,
        "nativePeer": None,
    }
    import io
    import json

    assert read_connector_envelope(io.BytesIO(json.dumps(valid).encode() + b"\n")).mode == "runtime"
    invalid = [
        {**valid, "schemaVersion": 2},
        {**valid, "mode": "observation"},
        {**valid, "bearerToken": "secret-canary"},
        {**valid, "bind": {"host": "example.invalid", "port": 8765, "path": "/mcp"}},
    ]
    for value in invalid:
        with pytest.raises(ValueError) as error:
            read_connector_envelope(io.BytesIO(json.dumps(value).encode() + b"\n"))
        assert "secret-canary" not in str(error.value)


def test_bootstrap_requires_exact_identity_and_required_fields():
    from fixture_engineering.managed_bootstrap import read_connector_envelope

    import io
    import json

    value = {
        "schemaVersion": 3,
        "binding": binding(),
        "mode": "runtime",
        "bind": {"host": "127.0.0.1", "port": 8765, "path": "/mcp"},
        "bearerToken": "a" * 40,
        "connectorBuildId": "sha256:" + "c" * 64,
        "configuration": {},
        "secrets": [],
        "dataDirectory": None,
        "nativePeer": None,
    }
    invalid = [
        {**value, "extra": "rejected"},
        {**value, "binding": {**value["binding"], "generation": 2}},
        {**value, "nativePeer": {"unexpected": True}},
    ]
    for item in invalid:
        with pytest.raises(ValueError, match="invalidBootstrap"):
            read_connector_envelope(io.BytesIO(json.dumps(item).encode() + b"\n"))


def test_v3_startup_accepts_no_real_software_or_credentials():
    import io
    import json

    from fixture_engineering.managed_bootstrap import read_connector_envelope

    value = {
        "schemaVersion": 3,
        "mode": "runtime",
        "binding": binding(),
        "bind": {"host": "127.0.0.1", "port": 8765, "path": "/mcp"},
        "bearerToken": "a" * 40,
        "connectorBuildId": "sha256:" + "c" * 64,
        "configuration": {},
        "secrets": [],
        "dataDirectory": None,
        "nativePeer": None,
    }
    context = read_connector_envelope(io.BytesIO(json.dumps(value).encode() + b"\n"))
    assert context.binding == value["binding"]
    assert context.connectorBuildId == value["connectorBuildId"]
    assert context.dataDirectory is None
