# Rhino Provider

Rhino 8 engineering tools and workflow skills for OpenDesign. The root `plugin.json` owns plugin identity; generated Provider and Yak version copies are checked before building.

The Provider uses the official MCP SDK and exposes 70 typed tools, four resources/templates and two prompts. Metadata mode lists declarations without connecting to Rhino or accepting engineering calls. Engineering execution requires the compatible Windows Host, the native Bridge and Rhino build `8.26.25349.19001`. Other plugin clients can read supported standard components and static workflow skills.

Release staging builds a standard wheel from the final build-identity-patched Provider source and includes its distribution metadata. The isolated PyPA build consumes the Provider's locked `build` dependency group with archive hash verification; it does not resolve an unbounded backend at release time. Its version must match the root Manifest, Provider project and editable project entry in `uv.lock`; third-party runtime wheels retain their separate frozen URL/hash/tag checks. Editable install metadata and local wheel `direct_url.json` are not copied into artifacts.

From this `provider` directory run `uv sync --frozen --extra test` and `uv run --frozen --extra test python -m pytest -q`. The native project and its pinned `global.json` live at the parent plugin root. Build both native targets there before the native test suite:

```powershell
dotnet build native/RhinoMcpBridge.csproj -c Release -p:RestoreLockedMode=true
dotnet test native/tests/RhinoMcpBridge.Tests.csproj -c Release -p:RestoreLockedMode=true
```

The native registration test binds the production fixed pipe. Run that case only
while OpenDesign and Rhino are normally closed; do not let it compete with a live
Host or Bridge. Report any exclusion explicitly.

For a retained native mixed-approval BOX case, run from this Provider directory:

```powershell
pwsh -NoProfile -File scripts/run-live-contract.ps1 -ArtifactRoot $verifiedRhinoArtifactRoot -EvidencePath $verifiedRhinoNativeEvidencePath
```

The evidence file contains the unmodified official Thread call/result projections
under `before`, `mutation` and `after`, the actual `softwareBuild`, and the verified
release identity under `artifact`. The auditor checks metadata inventory binding,
terminal checkpoints, exact call/result pairing, explicit deny/no-execution receipt,
independent attributes/layers and the unchanged axis-aligned BOX geometry. It never
starts a Provider, obtains a token, authenticates the Bridge or performs engineering
actions. It requires a previously Host-verified artifact and genuine native exports;
it does not verify signatures itself. Its output covers this case only: it does not
complete T33, certify all 70 tools or replace the separate native lifecycle checks.
The negative auditor fixtures are synthetic and are not real Rhino evidence.

The `scripts/build-release.ps1` and `scripts/verify-release.ps1` accept `-PluginRoot` and retain runtime/toolchain options. The build also requires `-ReleaseSequence`; generate the root release input with the same explicit counter before building (see the repository release guide). Both use the common artifact finalizer and verifier. A requested ZIP output is paired with a sibling `.receipt.json`; neither existing file is overwritten. Verify an unsigned candidate with `-Unsigned`, or pass `-TrustedKeys` containing caller-controlled publisher-id to Ed25519 public-key-hex mappings for a signed receipt. `-ReceiptPath` can select an explicit receipt. The common unsigned candidate builder calls `scripts/build_plugin.py --plugin-root ... --stage-only --output ... --cache ...`, then independently finalizes and verifies the artifact. Candidate build verification does not prove real Rhino engineering acceptance or authorize Stable publication. RhinoCommon, Grasshopper and Eto software assemblies are supplied by Rhino and are excluded from the Yak payload.
