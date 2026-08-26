[CmdletBinding()]
param(
    [string]$DataDirectory = "$env:ProgramData\Spectarr Agent",
    [switch]$RemoveData
)

$ErrorActionPreference = "Stop"
$ServiceName = "SpectarrAgent"
$Service = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($Service) {
    if ($Service.Status -ne "Stopped") {
        Stop-Service -Name $ServiceName -Force
        $Service.WaitForStatus("Stopped", [TimeSpan]::FromSeconds(30))
    }
    & "$env:SystemRoot\System32\sc.exe" delete $ServiceName | Out-Host
    if ($LASTEXITCODE -ne 0) {
        throw "Could not remove the Spectarr Agent service"
    }
}

if ($RemoveData -and (Test-Path -LiteralPath $DataDirectory)) {
    Remove-Item -LiteralPath $DataDirectory -Recurse -Force
}
