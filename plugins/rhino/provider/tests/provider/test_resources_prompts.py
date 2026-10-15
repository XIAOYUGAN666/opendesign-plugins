"""Resources and prompts remain discoverable through a real MCP client."""

from __future__ import annotations

import pytest

import rhino_mcp
from tests.fixtures.mcp_client import connected_client


EXPECTED_RESOURCES = {"rhinoscript://modules"}
EXPECTED_TEMPLATES = {
    "rhinoscript://module/{module_name}",
    "rhinoscript://function/{function_name}",
    "rhino://guidance/{topic}",
}
EXPECTED_PROMPTS = {"asset_general_strategy", "rhinoscript_workflow"}


@pytest.mark.asyncio
async def test_resources_and_prompts_match_the_converted_catalog() -> None:
    async with connected_client(rhino_mcp.mcp) as client:
        resources = (await client.list_resources()).resources
        templates = (await client.list_resource_templates()).resource_templates
        prompts = (await client.list_prompts()).prompts

    assert {str(resource.uri) for resource in resources} == EXPECTED_RESOURCES
    assert {template.uri_template for template in templates} == EXPECTED_TEMPLATES
    assert {prompt.name for prompt in prompts} == EXPECTED_PROMPTS
