from typing import Any, Dict, List, Optional

from mcp.server.mcpserver import Context

from rhino_mcp.server import get_rhino_connection


def create_planar_region(
    ctx: Context,
    outer_curve_id: str,
    inner_curve_ids: Optional[List[str]] = None,
    name: Optional[str] = None,
) -> Dict[str, Any]:
    """Create one planar Brep face from closed coplanar boundary curves.

    The outer curve bounds the face and each distinct inner curve becomes one
    hole. Inner loops must be strictly inside the outer loop and mutually
    disjoint and nonnested. Input curves are preserved.

    Returns the native result_ids, count and message for the created geometry.
    Inspect the created objects independently for topology and measurements.
    Exceptions propagate as MCP tool errors.
    """
    params: Dict[str, Any] = {
        "outer_curve_id": outer_curve_id,
        "inner_curve_ids": inner_curve_ids or [],
    }
    if name is not None:
        params["name"] = name

    result = get_rhino_connection(ctx).send_command("create_planar_region", params)
    return {
        "success": True,
        "result_ids": result["result_ids"],
        "count": result["count"],
        "message": result["message"],
    }
