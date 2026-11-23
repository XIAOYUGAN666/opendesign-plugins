from typing import Any, Dict

from mcp.server.mcpserver import Context

from rhino_mcp.server import get_rhino_connection


def get_context(ctx: Context) -> Dict[str, Any]:
    """
    Get a bounded snapshot of where you stand in Rhino. Call this first.

    Returns, for the active document and active viewport only:
    - document_name (null when untitled) and document_path (withheld)
    - units, absolute_tolerance, angle_tolerance (degrees)
    - current_layer (full path) and layer_count
    - object_count and objects_by_type (top 8 types by count, descending)
    - selection_count and selected_ids (first 20)
    - viewport: name (also as viewport_name), projection ("perspective" or
      "parallel"), display_mode, camera_location and camera_target as
      [x, y, z], camera_up and lens_length
    - scene_bounding_box {min, max}, null when the document is empty
    - grasshopper_definition_count (open Grasshopper definitions)

    Reading the context changes nothing. For the layer hierarchy and
    per-layer counts use get_document_summary; for objects use get_objects.
    """
    return get_rhino_connection(ctx).send_command("get_context")
