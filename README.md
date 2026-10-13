# OpenDesign plugin sources

This repository starts with two development roots in `plugins/`: Dlubal RFEM and Rhino. Each root Manifest is the only human source of plugin identity. Existing release-input versions are retained as development inputs; this skeleton does not announce a new Stable release or provide installable assets.

Provider source and its independent project/lock/environment have not migrated here yet. Root scripts and tests use the Python 3.12 standard library, with no root Python project or environment.

```powershell
python -B -m unittest discover -s tests -p test_contract_import.py -v
```

Import public exported contracts with `python -B scripts/import_contracts.py <host-native-owner> <agent-owner> schemas --host-contract-schema <v3-owner> --configuration-schema <configuration-owner> --official-schema <official-1.0.0-copy>`. Inputs are explicit exported files; no runtime import is required. The original three positional arguments also work. The importer copies original bytes and generates `schemas/owner-contracts.json` with owner references, versions and fresh SHA-256 digests. Fixed schema copies use `-text` checkout attributes.

The native namespace `io.github.xiaoyugan666.native` is a Windows Host extension, not an Agent Plugins standard component. Declared entrypoints are future signed installation ZIP paths, not source-tree executables. These roots have no fake `mcp.json` endpoint. Startup envelope 2 and native routines are target declarations; schema acceptance does not establish that the existing Provider decoders or engineering clients support them.

A required API Key belongs to engineering connection readiness. Installing a plugin package does not require the user's key. Non-native clients can load only standard components they support; engineering tools require a compatible Windows Host, real software and any required credentials. Skills and configurations grant no execution permission. This skeleton currently includes no Skills or Provider runtime.

`published/` will contain only automatically generated views from approved immutable Stable inputs. The formal marketplace will point to `./published/<id>` in the same main Git tree; it must never copy or scan current development roots as formal discovery. No Stable view or marketplace is present because no unified-repository Stable release is approved. The generator belongs to T08 and the first formal view to T35. Runtime and Yak assets remain outside Git history.

Never add credentials, private signing keys, business models, cache/runtime data or binaries to public source. See [AGENTS.md](AGENTS.md) for owner and verification boundaries.
