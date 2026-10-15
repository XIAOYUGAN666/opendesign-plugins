param(
  [Parameter(Mandatory = $true)][string]$ArtifactRoot
)

$ErrorActionPreference = 'Stop'

if (-not [Environment]::UserInteractive) {
  throw 'The real RFEM gate requires an interactive console for secure key input.'
}

uv run --frozen --extra test python tests/live/test_rfem_complete_contract.py --artifact-root $ArtifactRoot
exit $LASTEXITCODE
