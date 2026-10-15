param(
    [string]$PluginRoot,
    [string]$ArchivePath = "dist\rhino-0.1.2-windows-x86_64.zip",
    [string]$DotnetExe,
    [string]$UvExe,
    [string]$ReceiptPath,
    [switch]$Unsigned,
    [string]$TrustedKeys
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

$archive = if ([System.IO.Path]::IsPathRooted($ArchivePath)) {
    $ArchivePath
}
else {
    Join-Path $projectRoot $ArchivePath
}
if (-not (Test-Path -LiteralPath $archive -PathType Leaf)) {
    throw "Release archive not found."
}

$selectedUv = (Resolve-Path -LiteralPath $selectedUv).Path
$selectedDotnet = (Resolve-Path -LiteralPath $selectedDotnet).Path

$previousUv = $env:RHINO_UV_EXE
$previousDotnet = $env:RHINO_DOTNET_EXE
Push-Location $projectRoot
try {
    $env:RHINO_UV_EXE = $selectedUv
    $env:RHINO_DOTNET_EXE = $selectedDotnet
    $arguments = @("run", "--frozen", "--extra", "test", "python", "-B", "-m", "scripts.release_cli", "verify", "--archive", $archive, "--plugin-root", $PluginRoot, "--dotnet-exe", $selectedDotnet)
    if ($ReceiptPath) { $arguments += @("--receipt", $ReceiptPath) }
    if ($Unsigned) { $arguments += "--unsigned" }
    if ($TrustedKeys) { $arguments += @("--trusted-keys", $TrustedKeys) }
    & $selectedUv @arguments
    if ($LASTEXITCODE -ne 0) { throw "Rhino release verification failed with exit code $LASTEXITCODE." }
}
finally {
    Pop-Location
    $env:RHINO_UV_EXE = $previousUv
    $env:RHINO_DOTNET_EXE = $previousDotnet
}
