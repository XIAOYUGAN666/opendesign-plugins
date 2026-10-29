import io
import json
from pathlib import Path

import pytest
from fastmcp import Client

from dlubal_rfem_mcp.core.server import build_mcp
from dlubal_rfem_mcp.managed_bootstrap import read_connector_envelope


@pytest.mark.asyncio
async def test_eight_runtime_tools_preserve_verified_official_contract(envelope):
    fixture = json.loads(
        (Path(__file__).parent / "fixtures" / "tool-catalog-v1.json").read_text(
            encoding="utf-8"
        )
    )
    async with Client(
        build_mcp(read_connector_envelope(io.BytesIO(json.dumps(envelope).encode())))
    ) as client:
        tools = await client.list_tools()
    actual = []
    for tool in sorted(tools, key=lambda item: item.name):
        wire = tool.model_dump(by_alias=True, exclude_none=True)
        actual.append(
            {
                key: wire[key]
                for key in (
                    "name",
                    "description",
                    "inputSchema",
                    "outputSchema",
                    "annotations",
                )
                if key in wire
            }
        )
    from jsonschema import ValidationError, validate

    expected = fixture["tools"]
    for observed, original in zip(actual, expected, strict=True):
        if original["name"] == "get_rfem_application_info":
            schema = observed.pop("outputSchema")
            original.pop("outputSchema")
            validate({"status": "ok", "version": "6.14.0013"}, schema)
            validate({"status": "error", "failureCategory": "quotaRestricted"}, schema)
            with pytest.raises(ValidationError):
                validate(
                    {"status": "error", "failureCategory": "secret-vendor-text"}, schema
                )
        assert observed == original
    assert (
        fixture["sourceSha256"]
        == "947fac2c183061a79bdb59ddab053a30aebd8ea7d23702202bf516c42c20b0b7"
    )
