---
name: fixture-workflow
description: Internal synthetic MCP workflow validation only.
---
Use echo for a read-only check. Call synthetic_write only after explicit approval
and only in the isolated fixture execution process. Never use this fixture to
claim engineering correctness, access vendor software, or publish Stable assets.
Metadata mode permits discovery only and rejects every tool invocation.
