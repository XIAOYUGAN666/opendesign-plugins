from typing import Any, Dict, List, Literal, Optional

from mcp.server.mcpserver import Context
from mcp.types import ToolAnnotations

from rhino_mcp.server import get_rhino_connection
from rhino_mcp.tools._params import given


def set_camera(
    ctx: Context,
    viewport: Optional[str] = None,
    location: Optional[List[float]] = None,
    target: Optional[List[float]] = None,
    up: Optional[List[float]] = None,
    lens_length: Optional[float] = None,
    projection: Optional[Literal["perspective", "parallel"]] = None,
) -> Dict[str, Any]:
    """
    Point a viewport's camera. Changes what the user sees, never the document:
    no undo record is created, so undo still reverts the last geometry edit.

    Parameters (pass at least one besides viewport; fields left out keep
    their current value):
    - viewport: viewport name such as "Perspective" or "Top"; default active
    - location: camera location [x, y, z]
    - target: point the camera looks at [x, y, z]
    - up: camera up vector [x, y, z]
    - lens_length: 35 mm equivalent lens length in millimetres (perspective)
    - projection: "perspective" or "parallel"

    Returns the viewport's state afterwards: viewport_name, projection,
    display_mode, camera_location, camera_target, camera_up, lens_length.
    To frame objects without computing a camera, use zoom_to.
    """
    params = given(
        viewport=viewport,
        location=location,
        target=target,
        up=up,
        lens_length=lens_length,
        projection=projection,
    )
    return get_rhino_connection(ctx).send_command("set_camera", params)
