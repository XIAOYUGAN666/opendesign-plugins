# Dlubal RFEM Connector

This package serves eight Dlubal RFEM tools over local MCP stdio. It includes the
RFEM source from [Dlubal MCP 1.0.28](ORIGIN.md). The signed OpenDesign entry uses
the official tool registry and keeps the execution journal in plugin data.

Install RFEM 6 before installing the plugin. Installation prepares a private
Python 3.12 environment for the newest installed RFEM, even when RFEM is closed.
The installer checks the exact RFEM build against Dlubal's PyPI SDK compatibility
metadata and caches the checked wheel. Start RFEM with its API enabled and save
the API address, port and Dlubal API Key in the plugin settings. The default is
`127.0.0.1:9000`. The application-version/SDK binding is established during
installation. If the RFEM software update requires a different SDK, uninstall
and reinstall the plugin.

```powershell
uv sync --frozen --extra test --python 3.12.10
uv lock --check
uv run --frozen --extra test python -B -m pytest -q
uv run --frozen --extra test ruff check dlubal_mcp tests scripts
uv run --frozen --extra test python -m mypy --follow-imports=silent dlubal_mcp/plugin_stdio.py dlubal_mcp/setup.py dlubal_mcp/sdk.py
pwsh -NoProfile -File scripts/build-plugin.ps1 -ReleaseSequence <publisher-counter> -OutputDirectory <fresh-external-directory> -CacheDirectory <ignored-cache-directory>
pwsh -NoProfile -File scripts/verify-plugin.ps1 -Artifact <built-zip>
```

Generate the release inputs from the root `plugin.json` before building. The
archive contains the embedded interpreter, dependency lock, signed-input
inventory, candidate configuration schema, SBOM and tool preview. The common
verifier launches the actual stdio binary and compares its MCP tool declarations
against the preview. Candidate signing and Stable publication use the existing
repository release process.
