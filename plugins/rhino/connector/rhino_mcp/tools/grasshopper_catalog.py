"""Grasshopper component catalog discovery tools."""

from typing import Any, Dict, List, Optional

from mcp.server.mcpserver import Context

from rhino_mcp.tools._grasshopper_common import send_grasshopper_command


def gh_search_components(
    ctx: Context,
    query: Optional[str] = None,
    category: Optional[str] = None,
    limit: int = 50,
) -> Dict[str, Any]:
    """Search the installed Grasshopper component library."""
    params: Dict[str, Any] = {"limit": limit}
    if query:
        params["query"] = query
    if category:
        params["category"] = category
    return send_grasshopper_command(ctx, "gh_search_components", params)


def gh_batch_search_components(
    ctx: Context,
    queries: List[str],
    max_matches: int = 5,
) -> Dict[str, Any]:
    """Search multiple Grasshopper component names and return ranked matches.

    The legacy results field keeps the best match for each query. Use matches or
    ambiguous to disambiguate names like "Square" or "Grid" by category,
    subcategory, nickname, or GUID before creating components.
    """
    return send_grasshopper_command(
        ctx,
        "gh_batch_search_components",
        {"queries": queries, "max_matches": max_matches},
    )


def gh_list_component_categories(ctx: Context) -> Dict[str, Any]:
    """List installed Grasshopper component categories and subcategories."""
    return send_grasshopper_command(ctx, "gh_list_component_categories", {})


def gh_get_available_components(
    ctx: Context,
    category: Optional[str] = None,
    include_description: bool = False,
    limit: int = 500,
) -> Dict[str, Any]:
    """Get installed Grasshopper components, optionally filtered by category."""
    params: Dict[str, Any] = {
        "include_description": include_description,
        "limit": limit,
    }
    if category:
        params["category"] = category
    return send_grasshopper_command(ctx, "gh_get_available_components", params)


def gh_get_component_type_info(
    ctx: Context,
    name: Optional[str] = None,
    guid: Optional[str] = None,
) -> Dict[str, Any]:
    """Inspect a Grasshopper component type before creating an instance."""
    params: Dict[str, Any] = {}
    if name:
        params["name"] = name
    if guid:
        params["guid"] = guid
    return send_grasshopper_command(ctx, "gh_get_component_type_info", params)


def gh_batch_get_component_type_info(
    ctx: Context,
    components: List[Dict[str, str]],
) -> Dict[str, Any]:
    """Inspect multiple Grasshopper component types and I/O maps in one call.

    Each selector needs name/component_name or guid/component_guid. Prefer GUIDs
    when search returns ambiguous names.
    """
    return send_grasshopper_command(
        ctx,
        "gh_batch_get_component_type_info",
        {"components": components},
    )
