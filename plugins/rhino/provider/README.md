# Rhino Provider

Rhino 8 engineering tools and workflow skills for OpenDesign. The root `plugin.json` owns plugin identity; generated Provider and Yak version copies are checked before building.

The Provider uses the official MCP SDK and exposes 70 typed tools, four resources/templates and two prompts. Metadata mode lists declarations without connecting to Rhino or accepting engineering calls. Engineering execution requires the compatible Windows Host, the native Bridge and Rhino build `8.26.25349.19001`. Other plugin clients can read supported standard components and static workflow skills.

From this `provider` directory run `uv sync --frozen --extra test` and `uv run --frozen --extra test python -m pytest -q`. The native project and its pinned `global.json` live at the parent plugin root. Build both native targets there before the native test suite:

```powershell
dotnet build native/RhinoMcpBridge.csproj -c Release -p:RestoreLockedMode=true
dotnet test native/tests/RhinoMcpBridge.Tests.csproj -p:RestoreLockedMode=true
```

The `scripts/build-release.ps1` and `scripts/verify-release.ps1` preserve their existing parameters and accept `-PluginRoot`. Both use the common artifact finalizer and verifier. A requested ZIP output is paired with a sibling `.receipt.json`; neither existing file is overwritten. Verify an unsigned candidate with `-Unsigned`, or pass `-TrustedKeys` containing caller-controlled publisher-id to Ed25519 public-key-hex mappings for a signed receipt. `-ReceiptPath` can select an explicit receipt. The common unsigned candidate builder calls `scripts/build_plugin.py --plugin-root ... --stage-only --output ... --cache ...`, then independently finalizes and verifies the artifact. Candidate build verification does not prove real Rhino engineering acceptance or authorize Stable publication. RhinoCommon, Grasshopper and Eto software assemblies are supplied by Rhino and are excluded from the Yak payload.
