<#
.SYNOPSIS
  my-bot daily health check
.DESCRIPTION
  Triggered by Windows Task Scheduler at 17:45 on trading days.
  Runs Python healthcheck.py to check startup markers + report files.
  Output: alerts/daily/YYYY-MM-DD.md
#>

$ErrorActionPreference = "Stop"
$ProjectRoot = "d:\AgentAlpha\my-bot"
$HealthScript = Join-Path $ProjectRoot "scripts\healthcheck.py"
$PythonExe = "d:\AgentAlpha\signal-factory\venv\Scripts\python.exe"

# Use system Python as fallback
if (-not (Test-Path $PythonExe)) {
    $PythonExe = "python"
}

# Run health check
try {
    Set-Location $ProjectRoot
    $OutputEncoding = [Text.Encoding]::UTF8
    [Console]::OutputEncoding = [Text.Encoding]::UTF8

    $Result = & $PythonExe $HealthScript 2>&1
    $ExitCode = $LASTEXITCODE

    # Log to alert directory
    $DateStr = Get-Date -Format "yyyy-MM-dd"
    $AlertDir = Join-Path $ProjectRoot "alerts\daily"
    New-Item -ItemType Directory -Force -Path $AlertDir | Out-Null

    if ($Result) {
        $Result | Out-File -FilePath (Join-Path $AlertDir "$DateStr-console.log") -Encoding UTF8
    }
} catch {
    Write-Error "Health check failed: $($_.Exception.Message)"
    exit 1
}

exit $ExitCode