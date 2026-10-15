# Rhino plugin development source

`plugin.json` is the sole human-maintained plugin identity. This root contains the independent [Provider](provider/README.md), its project and lock, [native Bridge](native/), typed contracts and the static [workflow Skill](skills/rhino-workflow/SKILL.md). The old main-repository source remains until verified replacement acceptance. This source checkout is not an installed runtime or a Stable release.

The common builder stages `runtime/python/python.exe` with `rhino_mcp` under its isolated site-packages and packages the actual dual-target Bridge Yak. Native inventory and archive hashes are generated from real frozen build inputs and checked against both assemblies, never placeholders. Provider configuration, lifecycle schemas and MCP preview are generated from Provider-owned declarations.

Startup envelope 2 is implemented by the migrated Provider. Controlled metadata mode lists 70 tools, one resource plus three templates and two prompts without a Bridge connection, and rejects all tool calls. Actual execution requires a compatible authenticated native lease. MCP resource generation/bearer and Bridge lease generation/token have separate owners; only a real still-live authenticated lease may be reused after prior work safely settles. Unknown dispatch is sticky and cannot be cleared by reconnecting or restarting the Provider.

Engineering tools require the compatible Windows Host and Rhino 8 build `8.26.25349.19001`. Configuration and Skills grant no execution authority. Common Host integration and live engineering postconditions remain separate acceptance gates. Other Agent Plugins clients can load only standard components they support; portable native engineering execution is not claimed.

Local component checks and two reproducible unsigned development candidates do not establish real Rhino acceptance or publication. `published/` and formal Marketplace discovery are generated only from approved immutable Stable inputs. Final public Stable Release and Marketplace updates still require publisher confirmation.
