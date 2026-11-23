---
name: rhino-snapshot
description: Capture the active Rhino viewport and report what the image shows
metadata:
  display-name: Rhino 视口快照
---

Call the `capture_viewport` tool once.

- If the same user message explicitly requests a display mode, pass it as `display_mode` (for example `Shaded`, `Wireframe`, `Ghosted`, `Rendered`). Pass nothing else.
- If the same user message supplies no display mode, call it with no arguments.



The tool returns the image followed by a JSON metadata block. Show the image, then report next to it in a few lines:

- viewport_name, display_mode, projection, and width x height;
- camera_location to camera_target, and lens_length;
- visible_object_count out of total_object_count;
- scene_bounding_box (min to max).

Call out anything suggesting the picture does not show the model: visible_object_count of 0 while total_object_count is above 0, or a camera target far outside the scene bounding box. In that case suggest `zoom_to` with target `all`, but do not run it. Do not call `set_camera` or otherwise change the viewport.

If the tool fails with `invalid_argument` for an unknown display mode, show the guidance line (it lists the available modes) and stop; do not retry with a guessed name.

For any other tool failure, show the actual error and stop without changing the view.
