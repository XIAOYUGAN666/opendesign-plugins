import os
import sys
from importlib.metadata import version
from pathlib import Path

import pytest
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

RFEM_TOOLS = {
    "health_check",
    "get_rfem_application_info",
    "inspect_rfem_api",
    "write_rfem_model",
    "calculate_rfem_model",
    "read_rfem_results",
    "run_rfem_script",
    "get_rfem_execution",
}


@pytest.mark.asyncio
async def test_official_tools_are_listed_without_credentials(tmp_path):
    env = {
        key: os.environ[key]
        for key in ("SystemRoot", "TEMP", "TMP")
        if key in os.environ
    }
    env["PLUGIN_DATA"] = str(tmp_path)
    transport = StdioTransport(
        command=sys.executable,
        args=["-m", "dlubal_mcp.plugin_stdio", "--metadata"],
        env=env,
        cwd=Path.cwd(),
    )
    async with Client(transport, timeout=15) as client:
        tools = {t.name: t for t in await client.list_tools()}
        assert tools.keys() == RFEM_TOOLS
        assert tools["get_rfem_application_info"].annotations.readOnlyHint is True
        assert "execution_id" in tools["run_rfem_script"].inputSchema["properties"]
        result = await client.call_tool(
            "get_rfem_application_info", {}, raise_on_error=False
        )
        assert result.is_error
    assert version("fastmcp").startswith("2.")
    assert not (tmp_path / "config.ini").exists()
