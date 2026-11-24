---
name: rhino-scene
description: Summarize the open Rhino document (units, layer, objects, selection, camera)
metadata:
  display-name: Rhino 场景速览
---

Call the `get_context` tool once, with no arguments. Do not call any other tool and do not change the document.

Then summarize the result for the user in at most six short lines:

1. Document name (or "untitled") and units, with the absolute tolerance.
2. Current layer and the total layer count.
3. Object count and the leading object types with their counts.
4. Selection count; list up to five selected ids if any.
5. Active viewport: name, projection, display mode, and where the camera looks (location to target).
6. Scene bounding box (min to max), or "empty document". Mention the Grasshopper definition count only when it is above zero.

If the tool fails, show its error text as given (it reads `code: message` followed by a guidance line) and stop. If the Bridge is unavailable, direct the user to the Rhino plugin Settings connection diagnostics in OpenDesign.
