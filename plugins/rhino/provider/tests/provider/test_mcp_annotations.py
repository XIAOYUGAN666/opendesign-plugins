"""Tool annotations are complete and preserve high-risk operation signals."""

from __future__ import annotations

import pytest
from pathlib import Path

import rhino_mcp
from tests.fixtures.mcp_client import connected_client


PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOCAL_HELPER_TOOLS = {
    "get_modeling_guidance",
    "get_module_functions",
    "get_rhinoscript_docs",
    "list_rhinoscript_modules",
    "search_rhinoscript_functions",
}


HIGH_RISK_TOOLS = {
    "run_command",
    "execute_rhinoscript_python_code",
    "execute_rhinocommon_csharp_code",
}
READ_ONLY_TOOLS = {
    "analyze_objects",
    "capture_viewport",
    "describe_capabilities",
    "get_commands",
    "get_document_summary",
    "get_modeling_guidance",
    "get_module_functions",
    "get_object_attributes",
    "get_object_info",
    "get_objects",
    "get_rhinoscript_docs",
    "get_selected_objects_info",
    "gh_batch_get_component_type_info",
    "gh_batch_search_components",
    "gh_get_available_components",
    "gh_get_canvas_state",
    "gh_get_component_info",
    "gh_get_component_type_info",
    "gh_get_document_info",
    "gh_get_graph",
    "gh_get_parameter_value",
    "gh_list_component_categories",
    "gh_list_components",
    "gh_search_components",
    "list_rhinoscript_modules",
    "measure_objects",
    "search_rhinoscript_functions",
    "section_profile",
}
DESTRUCTIVE_TOOLS = {
    "boolean_difference",
    "boolean_intersection",
    "boolean_union",
    "delete_layer",
    "delete_object",
    "execute_rhinocommon_csharp_code",
    "execute_rhinoscript_python_code",
    "gh_clear_canvas",
    "gh_clear_graph",
    "gh_delete_component",
    "gh_mutate_graph",
    "redo",
    "run_command",
    "undo",
}


@pytest.mark.asyncio
async def test_every_discovered_tool_has_complete_boolean_annotations() -> None:
    async with connected_client(rhino_mcp.mcp) as client:
        tools = (await client.list_tools()).tools

    assert len(tools) == 70
    for tool in tools:
        assert tool.description and tool.description.strip(), tool.name
        assert tool.annotations is not None, tool.name
        assert isinstance(tool.annotations.read_only_hint, bool), tool.name
        assert isinstance(tool.annotations.destructive_hint, bool), tool.name
        assert isinstance(tool.annotations.idempotent_hint, bool), tool.name
        assert isinstance(tool.annotations.open_world_hint, bool), tool.name

    by_name = {tool.name: tool for tool in tools}
    assert len(by_name) == 70
    command_schema_names = {
        path.stem
        for path in (PROJECT_ROOT.parent / "contracts" / "commands").glob("*.json")
    }
    assert set(by_name) - LOCAL_HELPER_TOOLS == command_schema_names
    assert LOCAL_HELPER_TOOLS <= READ_ONLY_TOOLS
    for name, tool in by_name.items():
        annotations = tool.annotations
        assert annotations.read_only_hint is (name in READ_ONLY_TOOLS), name
        assert annotations.destructive_hint is (name in DESTRUCTIVE_TOOLS), name
        assert annotations.idempotent_hint is (name in READ_ONLY_TOOLS), name
        assert annotations.open_world_hint is (name in HIGH_RISK_TOOLS), name

    assert HIGH_RISK_TOOLS <= by_name.keys()
    for name in HIGH_RISK_TOOLS:
        annotations = by_name[name].annotations
        assert annotations.read_only_hint is False
        assert annotations.destructive_hint is True
        assert annotations.open_world_hint is True
