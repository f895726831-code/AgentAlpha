<#
.SYNOPSIS
  Evolution Backlog weekly review + Memory Bridge scan
.DESCRIPTION
  1. Runs evolution_review.py to generate a weekly backlog health report.
  2. Runs memory_to_evolution_bridge.py to find memory files without evolution entries.
  Scheduled: Saturday 11:00
  Output: data/evolution/reviews/weekly-review-YYYY-MM-DD.md
#>

$ErrorActionPreference = "Stop"
$ProjectRoot = "d:\AgentAlpha\my-bot"
$ReviewScript = Join-Path $ProjectRoot "scripts\evolution_review.py"
$BridgeScript = Join-Path $ProjectRoot "scripts\memory_to_evolution_bridge.py"
$PythonExe = "d:\AgentAlpha\signal-factory\venv\Scripts\python.exe"

if (-not (Test-Path $PythonExe)) {
    $PythonExe = "python"
}

try {
    Set-Location $ProjectRoot
    $OutputEncoding = [Text.Encoding]::UTF8
    [Console]::OutputEncoding = [Text.Encoding]::UTF8

    Write-Host "=== Evolution Weekly Review ==="
    $Result = & $PythonExe $ReviewScript 2>&1
    $ExitCode = $LASTEXITCODE

    if ($Result) {
        Write-Host $Result
    }

    # ── Memory → Evolution Bridge ──
    Write-Host "`n=== Memory → Evolution Bridge ==="
    $BridgeResult = & $PythonExe $BridgeScript --days 7 --json 2>&1
    $BridgeExitCode = $LASTEXITCODE

    if ($BridgeResult) {
        # Parse and display summary
        $BridgeJson = $BridgeResult | ConvertFrom-Json
        $GapCount = $BridgeJson.gaps
        $MatchedCount = $BridgeJson.matched
        Write-Host "Scanned: $($BridgeJson.total_memories) new memory files (last 7 days)"
        Write-Host "Matched: $MatchedCount already in backlog"
        Write-Host "Gaps:    $GapCount without evolution entries"

        if ($GapCount -gt 0) {
            Write-Host "`nUnmatched memories (candidates for capture):"
            foreach ($g in $BridgeJson.gap_details) {
                Write-Host "  - $($g.name) [$($g.severity)] $($g.description.Substring(0, [Math]::Min(80, $g.description.Length)))..."
            }
        }

        # Save bridge report alongside weekly review
        $DateStr = Get-Date -Format "yyyy-MM-dd"
        $BridgeFile = Join-Path $ProjectRoot "data\evolution\reviews\bridge-$DateStr.json"
        $BridgeResult | Out-File -FilePath $BridgeFile -Encoding UTF8
        Write-Host "`nBridge report saved: $BridgeFile"
    }
} catch {
    Write-Error "Evolution review failed: $($_.Exception.Message)"
    exit 1
}

exit $ExitCode