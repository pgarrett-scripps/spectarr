[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$MsiPath,
    [Parameter(Mandatory = $true)]
    [ValidatePattern("^[A-Fa-f0-9]{64}$")]
    [string]$ExpectedSha256
)

$ErrorActionPreference = "Stop"
$ResolvedMsi = (Resolve-Path -LiteralPath $MsiPath).Path
$ActualSha256 = (Get-FileHash -LiteralPath $ResolvedMsi -Algorithm SHA256).Hash
if ($ActualSha256 -ne $ExpectedSha256) {
    throw "The installer SHA-256 checksum does not match the expected release checksum"
}

$Process = Start-Process -FilePath "$env:SystemRoot\System32\msiexec.exe" -ArgumentList "/i", "`"$ResolvedMsi`"", "/passive", "/norestart" -Verb RunAs -Wait -PassThru
if ($Process.ExitCode -ne 0) {
    throw "The Spectarr Agent upgrade failed with exit code $($Process.ExitCode)"
}

$InstallScript = Join-Path $env:ProgramFiles "Spectarr Agent\Install-Service.ps1"
$Restart = Start-Process -FilePath "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -ArgumentList "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$InstallScript`"" -Verb RunAs -Wait -PassThru
if ($Restart.ExitCode -ne 0) {
    throw "The agent was upgraded but its service could not be restarted"
}
