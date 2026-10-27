# RFEM execution

## Tool identity

The native tools are `health_check`, `get_rfem_application_info`, `inspect_rfem_api`, `write_rfem_model`, `calculate_rfem_model`, `run_rfem_script`, `get_rfem_execution`, and `read_rfem_results`. Select the currently attached connector-prefixed tool with the exact matching native identity. Never guess or persist a connector ID. An unavailable tool requires attachment or selection to be resolved through the plugin UI. Tool availability does not grant approval.

## Preflight

1. Confirm the task contract and evidence-gate status.
2. Use `health_check` when provider or RFEM reachability must be known.
3. Identify whether the task uses a new/dedicated model, an existing model, or read-only inspection.
4. If the provider is stopped, ask the user to start it through the existing Desktop/Tauri control plane. The Agent Server does not manage the process.
5. If RFEM is unavailable, stop RFEM work and report the actual state.

## Inspect the installed API

Before writing a script, query `inspect_rfem_api` for every uncertain:

- base-data/add-on activation field;
- RFEM object class and field;
- enum value and relationship field;
- create/read/calculate/result method signature;
- Timber Design configuration and result access path.

Use focused follow-up queries when the first response is broad. If the exact identifier remains unavailable, block that script instead of guessing. Online Dlubal documentation can explain concepts but does not override the installed API.

## Protect the model

- Use a new or dedicated model for new construction tasks.
- For an existing model, inspect only relevant objects, IDs, references, axes, units, and calculation state.
- Make incremental changes and preserve unrelated numbering.
- Do not call `delete_all_objects`, clear the model, or overwrite it unless the user explicitly asks and approves that destructive scope.
- Stop when model identity, axes, units, or references cannot be confirmed.

## Stage execution

Default stages:

1. base model — base data, nodes, lines/members/surfaces, materials, sections/thicknesses, supports, releases/connections;
2. design inputs — load cases, loads, combinations/design situations, analysis settings, Timber Design objects/configurations;
3. calculation and focused result extraction;
4. read-only postcondition and engineering validation.

Use a separate `run_rfem_script` call for each mutating stage. Combine stages only when the scope is small, postconditions are explicit, and a partial failure is easy to inspect.

Before every mutating call, state:

- purpose and evidence;
- objects read/created/modified/deleted;
- assumptions;
- expected object counts, IDs, calculation state, or other postconditions;
- partial-failure risk.

The existing runtime interrupt permits approve/reject for `run_rfem_script`. If the user wants a change, reject the pending call and propose a new one.

## Script contract

- Use only provider-injected `app`, `rfem`, `common`, `math`, orientation helpers, and confirmed API identifiers.
- Do not use imports, file or environment access, credentials, subprocesses, network access, or attempts to bypass validation/HITL.
- Keep the scope minimal.
- Print created/modified object IDs, counts, calculation status, and planned postconditions.
- Do not treat the absence of an exception as proof that engineering postconditions hold.

## Failure and recovery

The one-shot worker is not a transaction and does not guarantee rollback or calculation cancellation. A script can mutate RFEM before failing.

On error, timeout, busy response, truncation, or cleanup warning:

1. stop automatic retry;
2. inspect the smallest relevant partial state;
3. compare actual state with planned postconditions;
4. surface timeout, truncation, busy, and cleanup warnings;
5. propose preserve, repair, or rollback-by-explicit-change options;
6. submit every new mutation through HITL again.

If the user changes the RFEM model between stages and postconditions no longer match, re-inspect and replan.
