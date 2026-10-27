# Task routing and task contract

## Classify the request

Classify an activated request as one or more of:

- create a new RFEM timber model;
- inspect or audit an existing model;
- modify an existing model;
- run structural analysis or Timber Design;
- troubleshoot modeling, calculation, or design results;
- perform explicitly bounded optimization.

Do not use this workflow for timber-only questions without an RFEM goal, RFEM tasks without timber content, carpentry/furniture work, Dlubal installation or MCP lifecycle control, or pure knowledge-base administration.

## Build the task contract

Record only fields relevant to the current task:

- objective, deliverables, and task type;
- new, dedicated, or existing RFEM model;
- geometry, topology, member/surface objects, and coordinate assumptions;
- materials, sections/thicknesses, connections/releases, and supports;
- location, use, design working life, importance, and seismic context;
- load sources, load cases, combinations, and design situations;
- analysis type, second-order effects, stability, and deformation requirements;
- Timber Design members/member sets/surfaces and required checks;
- user-fixed constraints, allowed assumptions, and acceptance criteria;
- model-protection and optimization authorization.

## Assign missing information

| Owner | Examples | Action |
| --- | --- | --- |
| User | site, use, geometry, connection intent, fixed constraints, permitted model changes | Ask a concise question before the affected decision |
| Standards evidence | applicability, load/design requirements, combinations, limits | Delegate to `knowledge-researcher`; verify its cited `[E<n>]` evidence |
| Installed RFEM API | exact class, field, enum, relation, method signature | Ask `inspect_rfem_api` |
| Existing RFEM model | current objects, IDs, axes, units, calculation state | Use the smallest approved inspection script |

Ask only when the missing answer can change the model, loads, combinations, analysis, Timber Design, safety, or authorization. Batch closely related user questions.

## Route by model state

### New model

Confirm that RFEM is empty or that the user selected a dedicated model. Do not clear an unrelated open model. Establish axes and units before object creation.

### Existing model

Default to read-only inspection. Identify the smallest relevant object scope and current calculation state. Propose incremental modifications with explicit object IDs and postconditions.

### Troubleshooting

Inspect the actual failure and partial state before proposing repair. Separate RFEM connection/tool errors, invalid API identifiers, modeling mechanisms, load/combination errors, calculation warnings, and Timber Design configuration gaps.

## Optimization contract

Do not optimize unless the user explicitly asks. Record:

- variables allowed to change;
- geometry, loads, materials, supports, and other fixed constraints;
- candidate values or numeric bounds;
- objective and pass/fail target;
- maximum iterations;
- per-iteration reporting and stop conditions.

Changing an unlisted input requires a new user decision. A failed check is a result to report, not permission to change the structure.
