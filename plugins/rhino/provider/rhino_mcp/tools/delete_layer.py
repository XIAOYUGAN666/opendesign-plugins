from mcp.server.mcpserver import Context
from rhino_mcp.bridge import BridgeError
from rhino_mcp.server import get_rhino_connection, logger


def delete_layer(
    ctx: Context,
    # Keep these declarations aligned with the existing MCP schema; None is the omission default.
    guid: str = None,  # type: ignore[assignment]
    name: str = None,  # type: ignore[assignment]
) -> str:
    """
    Delete a layer in the Rhino document.
    If name is provided, it will try to delete the layer with the given name.
    If guid is provided, it will try to delete the layer with the given guid.
    If neither is provided, it will return an error.
    
    Parameters:
    - name: The name of the layer to delete.
    - guid: The guid of the layer to delete.
    
    Returns:
    A message indicating the layer was deleted.
    
    Examples of params:
    - name: "Layer 1"
    - guid: "00000000-0000-0000-0000-000000000000"
    """
    try:
        # Get the global connection
        rhino = get_rhino_connection(ctx)

        command_params = {}

        if name is not None:
            command_params["name"] = name
        if guid is not None:
            command_params["guid"] = guid

        # Create the layer
        result = rhino.send_command("delete_layer", command_params)

        return result["message"]
    except BridgeError:
        raise
    except Exception as e:
        logger.error(f"Error deleting layer: {str(e)}")
        return f"Error deleting layer: {str(e)}"
