---
name: rhino-modeling-guide
description: Inspect, construct, and verify Rhino geometry using the bundled version 3 modeling guide.
metadata:
  display-name: Rhino 建模指南
---

Call `get_context` before the first edit and again after large changes. Inspect existing user work before changes. Call `get_modeling_guidance` with `overview` or the relevant bundled topic (`transforms`, `planar_regions`, `organization`, `verification`, or `recovery`). The published MCP resource `rhino://guidance/{topic}` serves the same text. Use `transforms` before composing world poses.

Verify the geometry against the request. When reviewing a viewport image, check its visible object count, scene bounding box, and camera. Act on the tool's error and guidance. A lost response after dispatch can leave the result unknown. Preserve that state for inspection and avoid replaying the write.

Respect the connector's authorized tools and approvals, including stronger controls for general commands and scripts. If the Bridge is unavailable, use the Rhino plugin's Settings connection and diagnostics, and have the user open Rhino normally when activation is pending.
