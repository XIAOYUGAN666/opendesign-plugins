import io
import json
import logging

import pytest
from fastmcp import Client
from test_origin_boundary import TOOLS

from dlubal_rfem_mcp.core.logging_config import JSONFormatter
from dlubal_rfem_mcp.core.server import build_mcp
from dlubal_rfem_mcp.managed_bootstrap import read_envelope


@pytest.mark.asyncio
async def test_managed_discovery_has_all_eight_tools_without_connecting_rfem(
    envelope, monkeypatch
):
    from dlubal.api.rfem import application

    def forbidden(*args, **kwargs):
        raise AssertionError("discovery must not open RFEM")

    monkeypatch.setattr(application, "Application", forbidden)
    mcp = build_mcp(read_envelope(io.BytesIO(json.dumps(envelope).encode())))
    async with Client(mcp) as client:
        tools = await client.list_tools()
        assert {tool.name for tool in tools} == TOOLS
        assert all(tool.description and tool.input_schema for tool in tools)
        health = await client.call_tool("health_check", {}, raise_on_error=False)
        assert health.is_error
        assert "softwareUnavailable" in str(health.content)


def test_logging_never_renders_secrets_arguments_paths_or_tracebacks():
    record = logging.LogRecord(
        "third-party",
        logging.ERROR,
        "private/path",
        1,
        "secret=%s",
        ("fixture-secret",),
        None,
    )
    record.api_key = "fixture-secret"
    rendered = JSONFormatter().format(record)
    assert "fixture-secret" not in rendered
    assert "private/path" not in rendered
    assert json.loads(rendered)["event"] == "runtimeLog"
