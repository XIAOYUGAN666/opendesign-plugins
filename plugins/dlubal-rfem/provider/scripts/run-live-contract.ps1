param(
  [Parameter(Mandatory = $true)][string]$ArtifactRoot,
  [Parameter(Mandatory = $true)][string]$ArtifactArchive
)

$ErrorActionPreference = 'Stop'

if (-not [Environment]::UserInteractive) {
  throw 'The real RFEM gate requires an interactive console for secure key input.'
}

$resolvedArtifactRoot = (Resolve-Path -LiteralPath $ArtifactRoot).ProviderPath
$resolvedArtifactArchive = (Resolve-Path -LiteralPath $ArtifactArchive).ProviderPath
Push-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
try {
  uv run --frozen --extra test python -B tests/live/test_rfem_complete_contract.py --artifact-root $resolvedArtifactRoot --artifact-archive $resolvedArtifactArchive
  $gateExit = $LASTEXITCODE
}
finally {
  Pop-Location
}
exit $gateExit
