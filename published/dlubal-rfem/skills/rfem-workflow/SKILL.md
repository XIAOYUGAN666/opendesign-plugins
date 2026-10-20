---
name: rfem-workflow
description: Guide RFEM 6 workflows with the available MCP tools.
---

Use the authenticated RFEM MCP tools only when the compatible Host has configured the required Dlubal API Key and the exact supported RFEM build is available. Metadata discovery and signed previews do not prove an engineering connection.

Verify the connection with get_rfem_application_info. Inspect typed API fields before preparing model objects. Ask for approval for model creation, calculation and scripts according to the client's tool policy. Use SI units and explicit model GUIDs; inspect existing models before changes.

A timed-out execution can still be running. Read get_rfem_execution with the same execution ID after connection verification. Never repeat a write, calculation or script to recover an unknown result. Keep unknown results for operator inspection. Never request, print or persist API keys in model messages or files.

This workflow is static guidance and grants no tool permissions. The separately imported timber engineering skill remains independent.
