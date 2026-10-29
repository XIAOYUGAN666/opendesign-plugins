"""Distribution-facing guide interfaces must agree and work without Rhino."""

import pytest
import rhino_mcp
from rhino_mcp.guidance import modeling_guidance, TOPICS, SERVER_INSTRUCTIONS
from rhino_mcp.prompts.assert_general_strategy import asset_general_strategy
from rhino_mcp.tools.get_modeling_guidance import (
    get_modeling_guidance,
    modeling_guidance_resource,
)


@pytest.mark.parametrize("topic", TOPICS)
def test_offline_interfaces_share_packaged_content(topic, monkeypatch):
    def fail():
        pytest.fail("Documentation attempted a Rhino connection")

    monkeypatch.setattr("rhino_mcp.server.get_rhino_connection", fail)
    result = get_modeling_guidance(topic)
    assert result["guidance_version"] == "2"
    assert result["package_version"]
    assert result["content"].startswith("# ")
    assert result["content"] == modeling_guidance_resource(topic)
    assert result["available_topics"] == list(TOPICS)
    if topic == "overview":
        assert asset_general_strategy() == result["content"]


@pytest.mark.parametrize(
    "topic", ["../server", "/etc/passwd", "https://example.com", "", "unknown"]
)
def test_unknown_topics_never_become_paths(topic):
    with pytest.raises(ValueError, match="Unknown guidance topic"):
        modeling_guidance(topic)


@pytest.mark.asyncio
async def test_mcp_discovery_exposes_readonly_guide_and_topic_enum():
    tool = next(
        t for t in await rhino_mcp.mcp.list_tools() if t.name == "get_modeling_guidance"
    )
    assert tool.annotations.read_only_hint is True
    assert tool.annotations.open_world_hint is False
    assert set(tool.input_schema["properties"]["topic"]["enum"]) == set(TOPICS)
    assert rhino_mcp.mcp.instructions == SERVER_INSTRUCTIONS
    templates = await rhino_mcp.mcp.list_resource_templates()
    assert "rhino://guidance/{topic}" in {str(t.uri_template) for t in templates}
