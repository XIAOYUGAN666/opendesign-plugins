param(
  [Parameter(Mandatory = $true)][string]$OutputDirectory,
  [Parameter(Mandatory = $true)][string]$CacheDirectory,
  [Parameter(Mandatory = $true)][ulong]$ReleaseSequence,
  [string]$PluginRoot = (Split-Path $PSScriptRoot -Parent | Split-Path -Parent)
)
$ErrorActionPreference = 'Stop'
uv run --frozen python -B -m scripts.build_plugin --plugin-root $PluginRoot --output $OutputDirectory --cache $CacheDirectory --release-sequence $ReleaseSequence
exit $LASTEXITCODE
