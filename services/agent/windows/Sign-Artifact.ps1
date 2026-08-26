[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Path,
    [Parameter(Mandatory = $true)]
    [string]$CertificateBase64,
    [Parameter(Mandatory = $true)]
    [string]$CertificatePassword
)

$ErrorActionPreference = "Stop"
$SignTool = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin\*\x64\signtool.exe" |
    Sort-Object FullName |
    Select-Object -Last 1
if (-not $SignTool) {
    throw "signtool.exe was not found"
}

$CertificatePath = Join-Path $env:RUNNER_TEMP "spectarr-windows-signing.pfx"
try {
    [IO.File]::WriteAllBytes(
        $CertificatePath,
        [Convert]::FromBase64String($CertificateBase64)
    )
    & $SignTool.FullName sign /fd SHA256 /f $CertificatePath /p $CertificatePassword /tr http://timestamp.digicert.com /td SHA256 $Path
    if ($LASTEXITCODE -ne 0) {
        throw "Signing failed for $Path"
    }
} finally {
    if (Test-Path -LiteralPath $CertificatePath) {
        Remove-Item -LiteralPath $CertificatePath -Force
    }
}
