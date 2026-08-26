[CmdletBinding()]
param(
    [string]$ServerUrl,
    [string]$WatchPath,
    [string]$AgentId,
    [Security.SecureString]$AgentToken,
    [string]$InstrumentId,
    [string]$ConfigPath = "$env:ProgramData\Spectarr Agent\agent.toml"
)

$ErrorActionPreference = "Stop"

$Identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$Principal = [Security.Principal.WindowsPrincipal]::new($Identity)
if (-not $Principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    if ($ServerUrl -or $WatchPath -or $AgentId -or $AgentToken -or $InstrumentId) {
        throw "Run parameterized configuration from an elevated PowerShell session"
    }
    Start-Process -FilePath "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -ArgumentList "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"" -Verb RunAs
    exit
}

function Read-RequiredValue {
    param([string]$Prompt, [string]$Value)

    if ($Value) {
        return $Value
    }
    $Result = Read-Host $Prompt
    if (-not $Result) {
        throw "$Prompt is required"
    }
    return $Result
}

function ConvertFrom-SecureValue {
    param([Security.SecureString]$Value)

    $Pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Value)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($Pointer)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($Pointer)
    }
}

function ConvertTo-TomlString {
    param([string]$Value)

    return '"' + $Value.Replace('\', '\\').Replace('"', '\"') + '"'
}

$ServerUrl = Read-RequiredValue "Spectarr server URL" $ServerUrl
$WatchPath = Read-RequiredValue "Acquisition folder" $WatchPath
$AgentId = Read-RequiredValue "Agent ID from the Spectarr dashboard" $AgentId
if (-not $AgentToken) {
    $AgentToken = Read-Host "Agent token from the Spectarr dashboard" -AsSecureString
}
$PlainToken = ConvertFrom-SecureValue $AgentToken
if (-not $PlainToken) {
    throw "Agent token is required"
}
if (-not $InstrumentId) {
    $InstrumentId = Read-Host "Instrument ID, or press Enter to leave it unset"
}

$DataDirectory = Split-Path -Parent $ConfigPath
$LogDirectory = Join-Path $DataDirectory "logs"
New-Item -ItemType Directory -Path $DataDirectory -Force | Out-Null
New-Item -ItemType Directory -Path $LogDirectory -Force | Out-Null

$Lines = @(
    "[agent]"
    "server_url = $(ConvertTo-TomlString $ServerUrl)"
    "watch_paths = [$(ConvertTo-TomlString $WatchPath)]"
    'state_db = "C:/ProgramData/Spectarr Agent/queue.sqlite3"'
    'log_file = "C:/ProgramData/Spectarr Agent/logs/agent.log"'
    "log_max_bytes = 10485760"
    "log_backup_count = 5"
    "agent_name = $(ConvertTo-TomlString $env:COMPUTERNAME)"
    "agent_id = $(ConvertTo-TomlString $AgentId)"
    "agent_token = $(ConvertTo-TomlString $PlainToken)"
)
if ($InstrumentId) {
    $Lines += "instrument_id = $(ConvertTo-TomlString $InstrumentId)"
}
$Lines += @(
    ""
    "poll_interval_seconds = 10"
    "stability_seconds = 120"
    "heartbeat_interval_seconds = 30"
    "chunk_size_bytes = 8388608"
    "request_timeout_seconds = 60"
    "retry_base_seconds = 2"
    "retry_max_seconds = 300"
    "max_attempts = 0"
)

[IO.File]::WriteAllLines($ConfigPath, $Lines, [Text.UTF8Encoding]::new($false))
$PlainToken = $null

& (Join-Path $PSScriptRoot "Install-Service.ps1") -InstallDirectory $PSScriptRoot -ConfigPath $ConfigPath -DataDirectory $DataDirectory
Write-Host "Spectarr Agent is configured and running"
