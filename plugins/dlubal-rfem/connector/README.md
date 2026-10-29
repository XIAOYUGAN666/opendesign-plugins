# Dlubal RFEM Connector

This repository contains the current RFEM Connector source and its provenance in [ORIGIN.md](ORIGIN.md). The root `plugin.json` owns plugin identity; the Python project version is checked against it during build. The main application consumes its signed self-contained artifact. Stable versions and release history are maintained remotely by the plugin marketplace.

The package owns its Python 3.12.10 environment and frozen dependency graph. It targets RFEM `6.14.0013.249` with `dlubal.api==2.14.13`. No main application source, sibling Connector or global engineering environment is imported.

```powershell
uv sync --frozen --extra test --python 3.12.10
uv lock --check
uv run --frozen --extra test python -m pytest -q
uv run --frozen --extra test ruff check dlubal_rfem_mcp tests scripts
uv run --frozen --extra test mypy dlubal_rfem_mcp
pwsh -NoProfile -File scripts/build-plugin.ps1 -ReleaseSequence <publisher-counter> -OutputDirectory <fresh-external-directory> -CacheDirectory <ignored-cache-directory>
pwsh -NoProfile -File scripts/verify-plugin.ps1 -Artifact <built-zip>
```

The builder accepts `--plugin-root` for the parent root and requires an explicit `--release-sequence`. First generate root and existing Connector release inputs with the same counter through the repository's common builder (see the repository release guide); drift is rejected before staging. The final archive includes the root identity, static workflow skill, full official MCP Tool preview, typed startup/configuration/execution-status declarations, exact file inventory, frozen runtime/wheel hashes, SBOM and pending risk evidence. Candidate receipts are unsigned and authorize no publication. Native tools require a compatible Windows Host and real software; ordinary clients can consume their supported standard skill component.

Managed V2 startup uses anonymous stdin. `mode=metadata` needs no business key, software binding or execution directory; it lists all eight declarations and rejects every tool call before any RFEM Application construction. Metadata availability is not engineering readiness. Normal disconnected UI uses a signed preview.

`mode=execution` requires Host-owned `configuration.dlubal-api-key`, an exact software binding and `data.stateDirectory`. The execution journal is derived inside that directory. The key passes directly to the unmodified official SDK as `Application(api_key_value=...)`, never via a key name or environment/INI default. The approved SDK value-only path may internally read its INI for name lookup; tests use only a test-owned INI and verify it remains unchanged. Keys never enter arguments, environment, logs, previews, lifecycle frames or model state.

The shared Host controller requests one software-connection confirmation at connector startup and every 120 seconds while healthy; transient failures use bounded backoff. Missing credentials, a stopped RFEM process, invalid credentials and unsupported builds pause automatic checks until the user clicks “Reconnect” after addressing the cause. The adapter checks for a running RFEM process before opening a session, uses the existing serialized `RFEMSession`, and confirms the exact build through `Service.get_application_info`; the session closes through `Service.close_connection`. It does not call Core, model, calculation, result, script or export methods for connection status. Saving a key alone never reports a successful connection. The ordinary `get_rfem_application_info` tool remains a separate engineering capability and is not used as an Agent-side diagnostic route.

One OperationControl and execution journal own admission, background work and uncertainty. Bounded stdout ready/status frames contain only safe generation/sequence/state/in-flight facts; unreadable journals remain unverifiable and orphan operations remain unknown. A response timeout does not make an unfinished synchronous SDK call idle. Never replay an uncertain write or script.

`get_rfem_execution` reports a successful journal lookup as an ordinary MCP result even when the observed operation is `not_found`, `running`, `unknown` or `failed`. That reports the state without claiming engineering success or resolving uncertainty. Write/calculate/script responses still use the existing failure flag unless their engineering operation succeeded; unreadable lookups and connection/credential refusals remain errors. The RFEM registration is the sole script-tool implementation; its lifecycle tests exercise that real registration rather than a second generic dispatcher.

An RFEM gRPC `UNAUTHENTICATED` status can also carry a Dlubal license-server network failure. Classification inspects the real RPC detail, distinguishing the known unexpected-connection-close/network-error response as service unavailability rather than invalid credentials or subscription restrictions. It does not change the machine's proxy/TUN configuration, retry paid authentication, or weaken credential readiness.

Real software, native installation, signing trust and engineering acceptance are separate gates. The main application's T32 records the actual RFEM build and engineering write/calculate/read-back postconditions; T35 records approved public artifact, Host verification and installation. Deterministic checks alone do not establish those outcomes. The separately sourced timber-engineering skill remains independent.
