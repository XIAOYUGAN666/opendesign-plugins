# Dlubal RFEM Provider

Independent RFEM-only Provider source migrated with the provenance in [ORIGIN.md](ORIGIN.md). The plugin identity is the parent `plugin.json`; the Python project version is checked against it during build. This candidate is not a Stable release. Historical P1 acceptance of the former package does not establish acceptance of this migrated V2 runtime.

The package owns its Python 3.12.10 environment and unchanged frozen lock. It targets RFEM `6.14.0013.249` with `dlubal.api==2.14.13`. No main application source, sibling Provider or global engineering environment is imported.

```powershell
uv sync --frozen --extra test --python 3.12.10
uv lock --check
uv run --frozen --extra test python -m pytest -q
uv run --frozen --extra test ruff check dlubal_rfem_mcp tests scripts
uv run --frozen --extra test mypy dlubal_rfem_mcp
pwsh -NoProfile -File scripts/build-plugin.ps1 -OutputDirectory <fresh-external-directory> -CacheDirectory <ignored-cache-directory>
pwsh -NoProfile -File scripts/verify-plugin.ps1 -Artifact <built-zip>
```

The builder accepts `--plugin-root` for the parent root and uses the repository's common release builder and independent verifier. The final archive includes the root identity, static workflow skill, full official MCP Tool preview, typed startup/configuration/lifecycle declarations, exact file inventory, frozen runtime/wheel hashes, SBOM and pending risk evidence. Candidate receipts are unsigned and authorize no publication. Native tools require a compatible Windows Host and real software; ordinary clients can consume their supported standard skill component.

Managed V2 startup uses anonymous stdin. `mode=metadata` needs no business key, software binding or execution directory; it lists all eight declarations and rejects every tool call before any RFEM Application construction. Metadata availability is not engineering readiness. Normal disconnected UI uses a signed preview.

`mode=execution` requires Host-owned `configuration.dlubal-api-key`, an exact software binding and `data.stateDirectory`. The execution journal is derived inside that directory. The key passes directly to the unmodified official SDK as `Application(api_key_value=...)`, never via a key name or environment/INI default. The approved SDK value-only path may internally read its INI for name lookup; tests use only a test-owned INI and verify it remains unchanged. Keys never enter arguments, environment, logs, previews, lifecycle frames or model state.

The explicit read-only application-info verification currently performs three SDK application-info RPCs (constructor, exact-build check, result) and a connection close; there is no automatic RPC polling or constructor retry. Ambiguous vendor restrictions remain `apiAccessRestricted`. Local journal tools reuse a real session verification fact without an extra RPC. Unverified or known-invalid facts block ordinary tools; explicit read-only verification can re-establish the connection.

One OperationControl and execution journal own admission, background work and uncertainty. Bounded stdout ready/status frames contain only safe generation/sequence/state/in-flight facts; unreadable journals remain unverifiable and orphan operations remain unknown. A response timeout does not make an unfinished synchronous SDK call idle. Never replay an uncertain write or script.

Real software, native installation, signing trust and engineering acceptance are separate gates. This migration's deterministic and artifact checks do not claim live RFEM acceptance. The separately sourced timber-engineering skill remains independent.
