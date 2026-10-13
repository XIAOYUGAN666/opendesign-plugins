# rhino development root

`plugin.json` is the sole human identity source. Its current version comes from the existing Provider release input; it is not a new unified Stable release. Provider migration is pending. The root contains no runtime, Skills, configuration export or MCP endpoint yet.

The declared `runtime/python/python.exe` entry and `rhino_mcp.managed_bootstrap` module refer to a future signed Windows installation ZIP. Existing Provider builders stage that relative interpreter and module today; this source root does not contain or execute them. Startup envelope 2 and `rhino-yak-bridge` are the approved future contract target, not proof of current decoder support. Configuration and static tool preview will be generated from Provider-owned inputs by later packaging work.

Engineering tools require a compatible Windows native Host, the exact software build declared in the extension, and any required credentials. Required secret slots gate engineering connection, not package installation. No secret value is stored in this root. Non-native clients load only supported standard components; no cross-client engineering execution is claimed.

Native component inventory and archive digests must come from actual future artifacts. This skeleton omits the optional native archive binding rather than inventing digest values. `published/` and formal marketplace discovery are generated only from approved Stable inputs, never from this current development root.
