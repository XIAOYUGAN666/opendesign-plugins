"""Grasshopper parameter value tools."""

from typing import Any, Dict, Optional

from mcp.server.mcpserver import Context

from rhino_mcp.tools._grasshopper_common import JsonValue, send_grasshopper_command


def gh_set_parameter_value(
    ctx: Context,
    value: JsonValue,
    instance_id: Optional[str] = None,
    nickname: Optional[str] = None,
    input_index: int = 0,
    input_name: Optional[str] = None,
    min: Optional[float] = None,
    max: Optional[float] = None,
    decimals: Optional[int] = None,
) -> Dict[str, Any]:
    """Set a slider, toggle, panel, value list, or regular input parameter value."""
    params: Dict[str, Any] = {"value": value}
    if instance_id:
        params["instance_id"] = instance_id
    if nickname:
        params["nickname"] = nickname
    if input_name:
        params["input_name"] = input_name
    else:
        params["input_index"] = input_index
    if min is not None:
        params["min"] = min
    if max is not None:
        params["max"] = max
    if decimals is not None:
        params["decimals"] = decimals
    return send_grasshopper_command(ctx, "gh_set_parameter_value", params)


def gh_get_parameter_value(
    ctx: Context,
    instance_id: Optional[str] = None,
    nickname: Optional[str] = None,
    output_index: int = 0,
    output_name: Optional[str] = None,
    max_items: int = 100,
) -> Dict[str, Any]:
    """Read structured data from a Grasshopper output parameter."""
    params: Dict[str, Any] = {"max_items": max_items}
    if instance_id:
        params["instance_id"] = instance_id
    if nickname:
        params["nickname"] = nickname
    if output_name:
        params["output_name"] = output_name
    else:
        params["output_index"] = output_index
    return send_grasshopper_command(ctx, "gh_get_parameter_value", params)
