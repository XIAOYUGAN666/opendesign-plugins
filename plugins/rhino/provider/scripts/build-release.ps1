param(
    [string]$PluginRoot,
    [string]$OutputPath,
    [string]$PythonRuntimeArchive,
    [string]$DotnetExe,
    [string]$UvExe
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not $PluginRoot) { $PluginRoot = Split-Path -Parent $projectRoot }
$PluginRoot = (Resolve-Path -LiteralPath $PluginRoot).Path
$projectRoot = Join-Path $PluginRoot "provider"
$selectedUv = $UvExe
if (-not $selectedUv) { $selectedUv = $env:RHINO_UV_EXE }
if (-not $selectedUv) {
    $uvCommand = Get-Command uv -ErrorAction SilentlyContinue
    if ($uvCommand) { $selectedUv = $uvCommand.Source }
}
if (-not $selectedUv -or -not (Test-Path -LiteralPath $selectedUv -PathType Leaf)) {
    throw "Set -UvExe or add uv to PATH."
}

$selectedDotnet = $DotnetExe
if (-not $selectedDotnet) { $selectedDotnet = $env:RHINO_DOTNET_EXE }
if (-not $selectedDotnet) {
    $dotnetCommand = Get-Command dotnet -ErrorAction SilentlyContinue
    if ($dotnetCommand) { $selectedDotnet = $dotnetCommand.Source }
}
if (-not $selectedDotnet -or -not (Test-Path -LiteralPath $selectedDotnet -PathType Leaf)) {
    throw "Set -DotnetExe or add the .NET 8 SDK host to PATH."
}

$selectedUv = (Resolve-Path -LiteralPath $selectedUv).Path
$selectedDotnet = (Resolve-Path -LiteralPath $selectedDotnet).Path

$previousUv = $env:RHINO_UV_EXE
$previousDotnet = $env:RHINO_DOTNET_EXE
Push-Location $projectRoot
try {
    $env:RHINO_UV_EXE = $selectedUv
    $env:RHINO_DOTNET_EXE = $selectedDotnet
    $arguments = @("run", "--frozen", "--extra", "test", "python", "-B", "-m", "scripts.release_cli", "build", "--plugin-root", $PluginRoot)
    if ($OutputPath) { $arguments += @("--output", $OutputPath) }
    if ($PythonRuntimeArchive) { $arguments += @("--python-runtime-archive", $PythonRuntimeArchive) }
    $arguments += @("--dotnet-exe", $selectedDotnet, "--uv-exe", $selectedUv)
    & $selectedUv @arguments
    if ($LASTEXITCODE -ne 0) { throw "Rhino release build failed with exit code $LASTEXITCODE." }
}
finally {
    Pop-Location
    $env:RHINO_UV_EXE = $previousUv
    $env:RHINO_DOTNET_EXE = $previousDotnet
}
