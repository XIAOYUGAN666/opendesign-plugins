param([Parameter(Mandatory = $true)][string]$Artifact)
$ErrorActionPreference = 'Stop'
uv run --frozen python -B -m scripts.verify_plugin $Artifact
exit $LASTEXITCODE
