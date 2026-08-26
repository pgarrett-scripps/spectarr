[CmdletBinding()]
param(
    [string]$InstallDirectory = $PSScriptRoot,
    [string]$ConfigPath = "$env:ProgramData\Spectarr Agent\agent.toml",
    [string]$DataDirectory = "$env:ProgramData\Spectarr Agent",
    [switch]$NoStart
)

$ErrorActionPreference = "Stop"
$ServiceName = "SpectarrAgent"
$Executable = Join-Path $InstallDirectory "spectarr-agent.exe"

function Assert-Administrator {
    $Identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $Principal = [Security.Principal.WindowsPrincipal]::new($Identity)
    if (-not $Principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Run this script from an elevated PowerShell session"
    }
}

function Invoke-ServiceControl {
    param([string[]]$Arguments)

    & "$env:SystemRoot\System32\sc.exe" @Arguments | Out-Host
    if ($LASTEXITCODE -ne 0) {
        throw "sc.exe failed with exit code $LASTEXITCODE"
    }
}

Assert-Administrator
if (-not (Test-Path -LiteralPath $Executable)) {
    throw "Agent executable not found at $Executable"
}
if (-not (Test-Path -LiteralPath $ConfigPath)) {
    throw "Agent configuration not found at $ConfigPath"
}

New-Item -ItemType Directory -Path $DataDirectory -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $DataDirectory "logs") -Force | Out-Null

$Service = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($Service -and $Service.Status -ne "Stopped") {
    Stop-Service -Name $ServiceName -Force
    $Service.WaitForStatus("Stopped", [TimeSpan]::FromSeconds(30))
}

$BinaryPath = '"' + $Executable + '" --windows-service --config "' + $ConfigPath + '"'
if ($Service) {
    Invoke-ServiceControl @("config", $ServiceName, "binPath=", $BinaryPath, "start=", "auto", "obj=", "NT AUTHORITY\LocalService")
} else {
    Invoke-ServiceControl @("create", $ServiceName, "binPath=", $BinaryPath, "start=", "auto", "obj=", "NT AUTHORITY\LocalService", "DisplayName=", "Spectarr Acquisition Agent")
}

Invoke-ServiceControl @("description", $ServiceName, "Watches completed instrument acquisitions and uploads them to Spectarr")
Invoke-ServiceControl @("failure", $ServiceName, "reset=", "86400", "actions=", "restart/10000/restart/30000/restart/60000")
Invoke-ServiceControl @("failureflag", $ServiceName, "1")
Invoke-ServiceControl @("sidtype", $ServiceName, "unrestricted")

& "$env:SystemRoot\System32\icacls.exe" $DataDirectory "/inheritance:r" "/grant:r" "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" "*S-1-5-19:(OI)(CI)M" | Out-Host
if ($LASTEXITCODE -ne 0) {
    throw "Could not secure $DataDirectory"
}

if (-not $NoStart) {
    Start-Service -Name $ServiceName
}
