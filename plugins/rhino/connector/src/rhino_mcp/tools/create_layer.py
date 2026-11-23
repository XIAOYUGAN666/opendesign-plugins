from mcp.server.mcpserver import Context
from rhino_mcp.bridge import BridgeError
from rhino_mcp.server import get_rhino_connection, logger
from typing import Any, List


def create_layer(
    ctx: Context,
    # Keep these declarations aligned with the existing MCP schema; None is the omission default.
    name: str = None,  # type: ignore[assignment]
    color: List[int] = None,  # type: ignore[assignment]
    parent: str = None,  # type: ignore[assignment]
) -> str:
    """
    Create a new layer in the Rhino document.

    Parameters:
    - name: The name of the new layer. If omitted, Rhino automatically generates the layer name.
    - color: Optional [r, g, b] color values (0-255) for the layer
    - parent: Optional parent reference. Use an exact full path such as "Assembly::Left"
      when names repeat; a simple name is accepted only when it identifies exactly one
      nondeleted layer. Missing or ambiguous parents and duplicate sibling names return errors.

    Returns:
    A message indicating the created layer name, or a clear creation error.

    Examples of params:
    - name: "Layer 1"
    - color: [255, 0, 0]
    - parent: "Assembly::Left"
    """
    try:
        # Get the global connection
        rhino = get_rhino_connection(ctx)

        command_params: dict[str, Any] = {}

        if name is not None:
            command_params["name"] = name
        if color is not None:
            command_params["color"] = color
        if parent is not None:
            command_params["parent"] = parent

        # Create the layer
        result = rhino.send_command("create_layer", command_params)

        return f"Created layer: {result['name']}"
    except BridgeError:
        raise
    except Exception as e:
        logger.error(f"Error creating layer: {str(e)}")
        return f"Error creating layer: {str(e)}"
