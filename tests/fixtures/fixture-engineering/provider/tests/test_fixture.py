from pathlib import Path
import pytest
from fastmcp import Client
from fixture_engineering.managed_bootstrap import build_mcp


@pytest.mark.asyncio
@pytest.mark.parametrize("requested,expected", [(None, "unknown"), ("idle", "idle")])
async def test_public_startup_reports_typed_default_unknown_or_explicit_idle(tmp_path, requested, expected):
    import asyncio
    import json
    import socket
    import sys
    from fixture_engineering.managed_bootstrap import ConfigurationV1, provider_build_id

    assert "lifecycleTestState" in ConfigurationV1.model_fields, "fixture has no typed lifecycle configuration"
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    envelope = {"schemaVersion": 2, "mode": "execution", "connectorInstanceId": "ci_" + "1" * 32, "generation": 1, "bind": {"host": "127.0.0.1", "port": port, "path": "/mcp"}, "bearerToken": "b" * 64, "providerBuildId": provider_build_id(Path(__file__).resolve().parents[1] / "fixture_engineering"), "data": {"stateDirectory": str(tmp_path)}, "software": {"safeIdentity": "si_fixture", "build": "synthetic-1"}}
    if requested:
        envelope["configuration"] = {"lifecycleTestState": requested}
    process = await asyncio.create_subprocess_exec(sys.executable, "-I", "-s", "-B", "-m", "fixture_engineering.managed_bootstrap", stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    try:
        process.stdin.write(json.dumps(envelope).encode()); await process.stdin.drain(); process.stdin.close()
        ready = json.loads(await asyncio.wait_for(process.stdout.readline(), 20))
        assert ready["kind"] == "ready"
        frame = json.loads(await asyncio.wait_for(process.stdout.readline(), 2))
        assert frame == {"schemaVersion": 1, "kind": "status", "connectorInstanceId": envelope["connectorInstanceId"], "generation": 1, "sequence": 1, "state": expected, "inFlight": 0}
    finally:
        if process.returncode is None: process.terminate()
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
        assert sorted(path.name for path in tmp_path.iterdir()) == [
            "synthetic-state.json"
        ]
        with pytest.raises(Exception):
            await client.call_tool(
                "synthetic_write", {"value": "x", "path": "../outside"}
            )


@pytest.mark.asyncio
async def test_metadata_startup_has_no_software_or_data_and_refuses_calls(tmp_path):
    from fixture_engineering.managed_bootstrap import StartupEnvelopeV2

    wire = {
        "schemaVersion": 2,
        "mode": "metadata",
        "connectorInstanceId": "ci_" + "1" * 32,
        "generation": 1,
        "bind": {"host": "127.0.0.1", "port": 8123, "path": "/mcp"},
        "bearerToken": "a" * 64,
        "providerBuildId": "sha256:" + "a" * 64,
    }
    accepted = True
    try:
        StartupEnvelopeV2.model_validate(wire)
    except ValueError:
        accepted = False
    assert accepted, "metadata must permit discovery without software/data"
    async with Client(
        build_mcp(wire["connectorInstanceId"], tmp_path, mode="metadata")
    ) as client:
        assert {tool.name for tool in await client.list_tools()} == {
            "echo",
            "synthetic_write",
        }
        for name in ("echo", "synthetic_write"):
            with pytest.raises(Exception):
                await client.call_tool(name, {"value": "must not execute"})
    assert not list(tmp_path.iterdir())


def test_startup_contract_rejects_unsafe_inputs_without_values_in_error():
    from fixture_engineering.managed_bootstrap import StartupEnvelopeV2

    wire = {
        "schemaVersion": 2,
        "mode": "metadata",
        "connectorInstanceId": "ci_" + "1" * 32,
        "generation": 1,
        "bind": {"host": "127.0.0.1", "port": 8123, "path": "/mcp"},
        "bearerToken": "a" * 64,
        "providerBuildId": "sha256:" + "a" * 64,
    }
    for key, value in (
        ("schemaVersion", 1),
        ("schemaVersion", True),
        ("mode", "unsafe-mode-canary"),
        ("bearerToken", "secret-canary"),
        ("bind", {"host": "example.invalid", "port": 8123, "path": "/mcp"}),
    ):
        with pytest.raises(ValueError) as error:
            StartupEnvelopeV2.model_validate({**wire, key: value})
        assert "secret-canary" not in str(error.value)
        assert "unsafe-mode-canary" not in str(error.value)
    with pytest.raises(ValueError):
        StartupEnvelopeV2.model_validate({**wire, "mode": "execution"})

@pytest.mark.parametrize("marker,expected", [(None, "unknown"), ("unknown", "unknown"), ("idle", "idle")])
@pytest.mark.asyncio
async def test_observation_is_data_only_and_requires_explicit_marker(tmp_path, marker, expected):
    import asyncio
    import json
    import socket
    import sys
    from fixture_engineering.managed_bootstrap import StartupEnvelopeV2, provider_build_id

    if marker is not None:
        (tmp_path / "observation-state.json").write_text(json.dumps({"state": marker}), encoding="utf-8")
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    wire = {"schemaVersion": 2, "mode": "observation", "connectorInstanceId": "ci_" + "1" * 32, "generation": 7, "bind": {"host": "127.0.0.1", "port": port, "path": "/mcp"}, "bearerToken": "b" * 64, "providerBuildId": provider_build_id(Path(__file__).resolve().parents[1] / "fixture_engineering"), "data": {"stateDirectory": str(tmp_path)}}
    envelope = StartupEnvelopeV2.model_validate(wire)
    assert envelope.mode == "observation"
    for extra in ({"configuration": {"lifecycleTestState": "idle"}}, {"software": {"safeIdentity": "si_fixture", "build": "synthetic-1"}}):
        with pytest.raises(ValueError):
            StartupEnvelopeV2.model_validate({**wire, **extra})
    with pytest.raises(ValueError):
        StartupEnvelopeV2.model_validate({k: v for k, v in wire.items() if k != "data"})
    process = await asyncio.create_subprocess_exec(sys.executable, "-I", "-s", "-B", "-m", "fixture_engineering.managed_bootstrap", stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    try:
        process.stdin.write(json.dumps(wire).encode()); await process.stdin.drain(); process.stdin.close()
        ready = json.loads(await asyncio.wait_for(process.stdout.readline(), 20))
        assert ready["mode"] == "observation"
        frame = json.loads(await asyncio.wait_for(process.stdout.readline(), 2))
        assert frame == {"schemaVersion": 1, "kind": "status", "connectorInstanceId": wire["connectorInstanceId"], "generation": 7, "sequence": 1, "state": expected, "inFlight": 0}
        async with Client(build_mcp(wire["connectorInstanceId"], tmp_path, mode="observation")) as client:
            for name in ("echo", "synthetic_write"):
                with pytest.raises(Exception):
                    await client.call_tool(name, {"value": "forbidden"})
    finally:
        if process.returncode is None: process.terminate()
        await asyncio.wait_for(process.wait(), 10)
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before
