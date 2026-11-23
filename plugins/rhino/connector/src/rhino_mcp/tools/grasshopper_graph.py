"""Grasshopper graph ownership tools."""

from typing import Any, Dict

from mcp.server.mcpserver import Context

from rhino_mcp.tools._grasshopper_common import send_grasshopper_command


def gh_get_graph(
    ctx: Context,
    graph_id: str,
    include_values: bool = False,
    max_items: int = 20,
) -> Dict[str, Any]:
    """Inspect all Grasshopper objects tagged with a graph id."""
    return send_grasshopper_command(
        ctx,
        "gh_get_graph",
        {
            "graph_id": graph_id,
            "include_values": include_values,
            "max_items": max_items,
        },
    )


def gh_clear_graph(
    ctx: Context,
    graph_id: str,
    include_groups: bool = True,
    recompute: bool = False,
) -> Dict[str, Any]:
    """Delete all Grasshopper objects tagged with a graph id."""
    return send_grasshopper_command(
        ctx,
        "gh_clear_graph",
        {
            "graph_id": graph_id,
            "include_groups": include_groups,
            "recompute": recompute,
        },
    )
