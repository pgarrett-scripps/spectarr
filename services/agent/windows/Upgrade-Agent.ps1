[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$MsiPath,
    [switch]$AllowUnsigned
)

$ErrorActionPreference = "Stop"
$ResolvedMsi = (Resolve-Path -LiteralPath $MsiPath).Path
$Signature = Get-AuthenticodeSignature -FilePath $ResolvedMsi
if (-not $AllowUnsigned -and $Signature.Status -ne "Valid") {
    throw "The installer signature is $($Signature.Status). Refusing to upgrade"
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
