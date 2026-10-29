import asyncio
import json
import socket
import sys

import pytest
from fastmcp import Client
from test_origin_boundary import TOOLS


@pytest.mark.asyncio
async def test_real_isolated_process_ready_authenticated_discovery_and_safe_call(
    envelope,
):
    with socket.socket() as port_probe:
        port_probe.bind(("127.0.0.1", 0))
        envelope["bind"]["port"] = port_probe.getsockname()[1]
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-I",
        "-s",
        "-m",
        "dlubal_rfem_mcp.managed_bootstrap",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    process_stdin = process.stdin
    process_stdout = process.stdout
    process_stderr = process.stderr
    assert process_stdin is not None
    assert process_stdout is not None
    assert process_stderr is not None
    try:
        process_stdin.write(json.dumps(envelope).encode() + b"\n")
        await process_stdin.drain()
        line = await asyncio.wait_for(process_stdout.readline(), 20)
        assert line, "Connector exited before readiness"
        ready = json.loads(line)
        assert ready == {
            "schemaVersion": 3,
            "kind": "ready",
            "binding": envelope["binding"],
            "connectorBuildId": envelope["connectorBuildId"],
            "port": envelope["bind"]["port"],
        }
        url = f"http://127.0.0.1:{ready['port']}/mcp"
        async with Client(url, auth=envelope["bearerToken"]) as client:
            tools = await client.list_tools()
            assert {tool.name for tool in tools} == TOOLS
            result = await client.call_tool(
                "inspect_rfem_api", {"query": "Material"}, raise_on_error=False
            )
            assert result.is_error
            assert "connectionUnverified" in result.content[0].text
    finally:
        if process.returncode is None:
            process.terminate()
        await asyncio.wait_for(process.wait(), 10)
        remaining = await process_stdout.read()
        stderr = await process_stderr.read()
        assert all(
            json.loads(frame)["kind"] == "status" for frame in remaining.splitlines()
        )
        assert envelope["bearerToken"].encode() not in stderr
        assert envelope["secrets"][0]["value"].encode() not in stderr
        assert envelope["dataDirectory"].encode() not in stderr
