import json

from mcp.server.mcpserver import Context

from rhino_mcp.server import get_rhino_connection


def get_document_summary(ctx: Context) -> str:
    """
    Get a lightweight summary of the current Rhino document.

    This returns aggregated statistics about the model without individual object details:
    - Document metadata (name, units, tolerance, dates)
    - Total object count
    - Object counts grouped by type (e.g., 50 curves, 30 surfaces)
    - Object counts grouped by layer
    - Model-wide bounding box encompassing all objects
    - Full layer hierarchy with parent-child relationships and per-layer object counts

    Use this tool first to understand the model composition before querying specific objects.
    For detailed object information, use get_objects with filters.
    """
    rhino = get_rhino_connection(ctx)
    result = rhino.send_command("get_document_summary")
    return json.dumps(result, indent=2)
