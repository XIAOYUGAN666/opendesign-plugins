"""Local documentation access; no Rhino command or document mutation."""

from typing import Any, Dict, Literal

from rhino_mcp.guidance import modeling_guidance


def get_modeling_guidance(
    topic: Literal[
        "overview",
        "transforms",
        "planar_regions",
        "organization",
        "verification",
        "recovery",
    ] = "overview",
) -> Dict[str, Any]:
    """Read the versioned Rhino modeling guide bundled with this server.

    Consult transforms for anchors and world-origin rotation, planar_regions for
    faces with holes, organization for layer assignment, verification for checking
    results, or recovery after uncertain edits. overview lists the workflow and
    topics. Works without a Rhino connection; does not change documents.
    """
    return modeling_guidance(topic)


def modeling_guidance_resource(topic: str) -> str:
    """Read the same bundled guide through an MCP resource."""
    return modeling_guidance(topic)["content"]
