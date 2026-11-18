<#
.SYNOPSIS
  Track B external source scan
.DESCRIPTION
  Generates scan templates for Tier 2 (journals + tools).
  After templates are generated, they should be reviewed and filled in
  by a human or LLM agent with WebSearch access.
  Scheduled: 1st of each month, 09:00
  Output: data/evolution/external_scans/scan-summary-YYYY-MM-DD.md
#>

$ErrorActionPreference = "Stop"
$ProjectRoot = "d:\AgentAlpha\my-bot"
$ScanScript = Join-Path $ProjectRoot "scripts\scan_external_sources.py"
$PythonExe = "d:\AgentAlpha\signal-factory\venv\Scripts\python.exe"

if (-not (Test-Path $PythonExe)) {
    $PythonExe = "python"
}

try {
    Set-Location $ProjectRoot
    $OutputEncoding = [Text.Encoding]::UTF8
    [Console]::OutputEncoding = [Text.Encoding]::UTF8

    $Result = & $PythonExe $ScanScript --tier 2 2>&1
    $ExitCode = $LASTEXITCODE

    if ($Result) {
        Write-Host $Result
    }
} catch {
    Write-Error "External scan failed: $($_.Exception.Message)"
    exit 1
}

exit $ExitCode