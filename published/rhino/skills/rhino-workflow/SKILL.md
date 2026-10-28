---
name: rhino-workflow
description: Guide Rhino 8 engineering workflows through the available MCP tools.
---

Engineering tools require the compatible Windows Host, the supported Rhino build and an authenticated matching native Bridge. Installed packages, metadata discovery and signed tool previews do not prove that the engineering connection is ready. Let the user normally start or restart Rhino when native activation is pending; do not close their software.

Inspect the document and relevant objects before preparing changes. Use explicit object identities, document units and bounded operations. Follow the client's tool permissions and approval flow. General Rhino commands and Python or C# scripts retain their stronger safety restrictions; this guidance grants no execution permission.

A lost response after command dispatch can leave the engineering result unknown. Do not repeat the operation, reconnect to clear the uncertainty, or report success from a transport reconnect. Preserve the unknown state for operator inspection of the isolated test model or affected document. Stopping the Provider does not prove that Rhino stopped an already dispatched action.

Use test documents for acceptance checks and inspect actual geometry or document changes after each operation. Do not overwrite everyday engineering files. This static workflow does not replace the Host, Agent approval system or independently imported Skills.
