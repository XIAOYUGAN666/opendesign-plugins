from pathlib import Path
import pytest
from fastmcp import Client
from fixture_engineering.managed_bootstrap import build_mcp


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
