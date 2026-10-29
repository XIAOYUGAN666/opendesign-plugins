"""Shared helpers for Grasshopper MCP tool wrappers."""

from typing import Any, Dict, List, Union

from mcp.server.mcpserver import Context

from rhino_mcp.server import get_rhino_connection


JsonValue = Union[float, int, str, bool, List[Any], Dict[str, Any]]


def send_grasshopper_command(
    ctx: Context, command: str, params: Dict[str, Any]
) -> Dict[str, Any]:
    rhino = get_rhino_connection(ctx)
    return rhino.send_command(command, params)
