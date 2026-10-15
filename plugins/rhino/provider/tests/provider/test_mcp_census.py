"""Freeze the MCP discovery surface exposed over the real client protocol."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import rhino_mcp
from tests.fixtures.mcp_client import connected_client


EXPECTED_CATALOG = Path(__file__).parents[1] / "fixtures" / "expected_mcp_catalog.json"


def _normalized_tool(tool) -> dict[str, object]:
    return {
        "name": tool.name,
        "description": tool.description,
        "inputSchema": tool.input_schema,
        "annotations": (
            tool.annotations.model_dump(by_alias=True, exclude_none=False)
            if tool.annotations is not None
            else None
        ),
    }


@pytest.mark.asyncio
async def test_mcp_discovery_matches_the_frozen_tool_catalog() -> None:
    expected = json.loads(EXPECTED_CATALOG.read_text(encoding="utf-8"))
    async with connected_client(rhino_mcp.mcp) as client:
        tools = (await client.list_tools()).tools
        actual = sorted(
            (_normalized_tool(tool) for tool in tools),
            key=lambda tool: str(tool["name"]),
        )

        assert actual == expected["tools"]
        assert len(tools) == 70

        resources = (await client.list_resources()).resources
        templates = (await client.list_resource_templates()).resource_templates
        assert len(resources) + len(templates) == 4

        prompts = (await client.list_prompts()).prompts
        assert len(prompts) == 2
