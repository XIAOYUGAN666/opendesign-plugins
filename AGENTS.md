# Plugin source boundaries

Development roots are `plugins/<id>/`. Each root `plugin.json` is the only human-maintained plugin identity (name, version, description, author). Derived release metadata and Provider/Yak version fields must be generated from, or checked against, that root. Do not create a second identity catalog.

Each future `plugins/<id>/provider/` owns its own Python project, lock, virtual environment, configuration, secrets and tests. The repository root is standard-library CLI tooling only: no root pyproject.toml, uv.lock or .venv. Use Python 3.12 with `-B`; CI will supply the CLI interpreter through setup-python. Do not import private Host/Agent runtime modules or sibling Providers.

Provider owns engineering API adaptation, typed tools, validation, software session and external software errors. Host owns installation, credentials, processes, ports and attachment. Agent owns reasoning, orchestration, MCP discovery and interrupt/resume. Communicate through standard MCP and exported owner contracts. Never move application runtime state into this repository.

`schemas/` contains fixed exported owner bytes. Change contracts at their authoritative Host or Agent owner, then run `scripts/import_contracts.py`; never edit copied fields or generate a parallel validator. `owner-contracts.json` is an automatically generated contract inventory, not plugin identity. Preserve .gitattributes byte rules and review fresh digests.

`published/` is an automatically generated approved Stable projection. Do not edit it or source formal discovery from current development roots. No approved Stable input is included here yet. The future generator is T08; the first approved view is T35. No marketplace or published placeholder may imply a release exists.

Never commit credentials, private signing keys, .env, business models, runtime state, caches, binaries or absolute private machine paths. Review the explicit public file list before staging. JSON schema metadata and secret-slot field declarations contain no secret values.

A required engineering API key gates engineering connection, not plugin installation. Other Agent Plugins clients can load only their supported standard components; native engineering tools require the compatible Windows Host and real software. Schema validation does not prove runnable assets or live engineering acceptance.

Verify from this root: `python -B -m unittest discover -s tests -v`. Run each migrated Provider's full checks from its own package when migration is authorized. Report real software and native acceptance separately. Stable public release requires the final publisher approval.
