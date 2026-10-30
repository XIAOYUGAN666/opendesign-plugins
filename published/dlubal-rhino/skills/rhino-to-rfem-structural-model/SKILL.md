---
name: rhino-to-rfem-structural-model
description: >-
  Activate ONLY when BOTH the source is Rhino AND the requested destination is an RFEM structural member model, or when reviewing/resuming that exact forward conversion. If either condition is absent, do not open this SKILL.md. Do not load for Rhino-only meshing/modelling, RFEM-only modelling/results, reverse RFEM-to-Rhino export, or plugin installation. 仅用于 Rhino 到 RFEM 完整杆系的正向转换及该转换的审查、恢复；不匹配时不要读取本技能。
---

# Rhino to RFEM structural member model

First check the requested direction and outcome. If the request is not this forward conversion or its review/resumption, do not apply this workflow or read its references. Continue with the relevant software task instead; the conversion's proposal and confirmation steps do not apply to unrelated work.

Guide a complete member-system conversion across any structural system, material, naming scheme or geometry. Identify the actual attached Rhino and RFEM MCP tools from their declarations. This skill supplies guidance, not tool permissions or a software connection.

1. For the task contract and source inventory read [task and scope](references/task-and-scope.md).
2. For geometry, member axes and section frames read [axis and section extraction](references/axis-and-section-extraction.md).
3. For structural classification, RFEM mapping and connectivity read [member mapping and topology](references/member-mapping-and-topology.md).
4. Before requesting confirmation, handling changes or resuming a paused task read [human review](references/human-review.md).
5. Before RFEM mutation, recovery or readback read [RFEM execution and validation](references/rfem-execution-and-validation.md).

Resume from the relevant stage after checking existing evidence and the validity of the confirmation. Account for every source object. Separate measured facts from assumptions and preserve traceability between source objects and target members. Do not infer structural behavior solely from appearance, names or proximity.

Present the complete conversion proposal and wait for explicit confirmation of that version before any RFEM model creation or mutation. Tool approval is a separate requirement. If relevant source geometry, attributes, transformation, mapping or target changes, show the differences and obtain a new confirmation. Preserve the Rhino source; preview modifications also require existing tool permissions.

For unknown, running or timed-out operations, stop writes and inspect the original operation through its owner. Never blindly replay, clear or switch models to hide uncertainty. Report incomplete work and missing capabilities explicitly. Calculation, loads and design checks are separate tasks.
