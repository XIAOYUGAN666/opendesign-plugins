param(
  [Parameter(Mandatory = $true)][string]$ArtifactRoot,
  [Parameter(Mandatory = $true)][string]$EvidencePath
)

$ErrorActionPreference = 'Stop'
$resolvedArtifactRoot = (Resolve-Path -LiteralPath $ArtifactRoot).ProviderPath
$resolvedEvidencePath = (Resolve-Path -LiteralPath $EvidencePath).ProviderPath
Push-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
try {
  uv run --frozen --extra test python -B tests/live/test_unified_rhino_contract.py --artifact-root $resolvedArtifactRoot --evidence-path $resolvedEvidencePath
  $gateExit = $LASTEXITCODE
}
finally {
  Pop-Location
}
exit $gateExit
