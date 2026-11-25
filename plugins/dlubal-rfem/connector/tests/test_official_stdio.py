import json
import os
import sys
from importlib.metadata import version
from pathlib import Path

import pytest
import yaml
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


def test_rfem_analyst_declares_all_rfem_tools():
    root = Path(__file__).resolve().parents[2]
    manifest = json.loads((root / "plugin.json").read_bytes())
    role_path = "agents/rfem-analyst.md"
    assert manifest["extensions"]["ai.opendesign.desktop"].get("agents") == {
        "schemaVersion": 1,
        "files": [role_path],
    }
    role = (root / role_path).read_text(encoding="utf-8")
    metadata = yaml.safe_load(role.split("---", 2)[1])
    assert metadata["name"] == "rfem-analyst"
    assert metadata["model"] == "inherit"
    references = [reference.strip() for reference in metadata["tools"].split(",")]
    assert sorted(references) == sorted(f"mcp__rfem__{name}" for name in RFEM_TOOLS)
