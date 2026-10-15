from mcp.server.mcpserver import Context
from rhino_mcp.server import get_rhino_connection, logger


def undo(ctx: Context, steps: int = 1) -> str:
    """
    Undo the last operation(s) in the Rhino document.

    Parameters:
    - steps: Number of operations to undo (default: 1)

    Returns:
    A message indicating how many operations were undone.
    """
    try:
        rhino = get_rhino_connection(ctx)
        result = rhino.send_command("undo", {"steps": steps})
        return result["message"]
    except Exception as e:
        logger.error(f"Error undoing: {str(e)}")
        return f"Error undoing: {str(e)}"


def redo(ctx: Context, steps: int = 1) -> str:
    """
    Redo the last undone operation(s) in the Rhino document.

    Parameters:
    - steps: Number of operations to redo (default: 1)

    Returns:
    A message indicating how many operations were redone.
    """
    try:
        rhino = get_rhino_connection(ctx)
        result = rhino.send_command("redo", {"steps": steps})
        return result["message"]
    except Exception as e:
        logger.error(f"Error redoing: {str(e)}")
        return f"Error redoing: {str(e)}"
