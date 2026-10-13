# OpenDesign plugin sources

This repository starts with two development roots in `plugins/`: Dlubal RFEM and Rhino. Each root Manifest is the only human source of plugin identity. Existing release-input versions are retained as development inputs; this skeleton does not announce a new Stable release or provide installable assets.

Provider source and its independent project/lock/environment have not migrated here yet. Root scripts and tests use the Python 3.12 standard library, with no root Python project or environment.

```powershell
python -B -m unittest discover -s tests -p test_contract_import.py -v
```

Import public exported contracts with `python -B scripts/import_contracts.py <host-native-owner> <agent-owner> schemas --host-contract-schema <v3-owner> --configuration-schema <configuration-owner> --official-schema <official-1.0.0-copy>`. Inputs are explicit exported files; no runtime import is required. The original three positional arguments also work. The importer copies original bytes and generates `schemas/owner-contracts.json` with owner references, versions and fresh SHA-256 digests. Fixed schema copies use `-text` checkout attributes.

The native namespace `io.github.xiaoyugan666.native` is a Windows Host extension, not an Agent Plugins standard component. Declared entrypoints are future signed installation ZIP paths, not source-tree executables. These roots have no fake `mcp.json` endpoint. Startup envelope 2 and native routines are target declarations; schema acceptance does not establish that the existing Provider decoders or engineering clients support them.

A required API Key belongs to engineering connection readiness. Installing a plugin package does not require the user's key. Non-native clients can load only standard components they support; engineering tools require a compatible Windows Host, real software and any required credentials. Skills and configurations grant no execution permission. This skeleton currently includes no Skills or Provider runtime.

`published/` will contain only automatically generated views from approved immutable Stable inputs. The generated official discovery file at `.agents/plugins/marketplace.json` currently has an empty `plugins` array: no Stable release, view or signed snapshot is approved. This is discovery structure only, not a signed empty Stable snapshot or a verified client UI claim. The first formal view remains T35. Discovery points to `./published/<id>` relative to this repository root, in the same main Git tree; it never scans current development roots. Runtime and Yak assets remain outside Git history.

The independent Windows CI prepares only each selected project's own frozen environment. RFEM and Rhino Provider projects have not migrated and are explicitly reported as `notPresent`, not as Provider test/build passes. The default manual candidate workflow validates/builds only; public Actions artifact uploads are restricted to synthetic fixture evidence. See [releasing.md](docs/releasing.md) for generator commands, signing inputs and final publication boundaries.

Never add credentials, private signing keys, business models, cache/runtime data or binaries to public source. See [AGENTS.md](AGENTS.md) for owner and verification boundaries.

The internal fixture lives at `tests/fixtures/fixture-engineering/`. From its `provider/`, run `uv sync --frozen --extra test --extra build` and `uv run --frozen --extra test python -B -m pytest -q`. Its independent builder stages a real locked Windows embedded CPython runtime and MCP declarations; it does not produce a signed release here. Startup version 2 metadata mode rejects calls; execution mode uses only isolated synthetic state. The fixture identity and test signer never enter Stable or production trust, and it provides no RFEM/Rhino readiness or engineering correctness evidence.
