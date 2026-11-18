<#
.SYNOPSIS
  my-bot El Niño weekly pipeline
.DESCRIPTION
  Triggered by Windows Task Scheduler every Monday at 08:00.
  Runs: ENSO data fetch → futures data → monitoring report.
  Logs: logs/cron/el-nino-pipeline.log
  Report: reports/el-nino/YYYY-MM-DD-el-nino-monitor.md
#>

$ErrorActionPreference = "Stop"
$ProjectRoot      = "d:\AgentAlpha\my-bot"
$LogDir           = Join-Path $ProjectRoot "logs\cron"
$ElNinoScript     = Join-Path $ProjectRoot "scripts\run_el_nino_pipeline.py"

$env:CLAUDE_CODE_GIT_BASH_PATH = "D:\Git\usr\bin\bash.exe"

# ─── Logging ──────────────────────────────────────────────────────────────
$LogFile = Join-Path $LogDir "el-nino-pipeline.log"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Write-Log {
    param([string]$Message)
    $Timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $Line = "$Timestamp $Message"
    Add-Content -Path $LogFile -Value $Line -Encoding UTF8
    Write-Host $Line
}

Write-Log "=== El Niño pipeline start ==="

# Session 0 fix: cd to project root
Set-Location $ProjectRoot

# ─── Execute pipeline ──────────────────────────────────────────────────────
$StartTime = Get-Date

try {
    Write-Log "RUN: fetch_enso + fetch_futures + generate_report"

    $OutputEncoding = [Text.Encoding]::UTF8
    [Console]::OutputEncoding = [Text.Encoding]::UTF8

    # Run the pipeline
    $Result = & python $ElNinoScript 2>&1
    $ExitCode = $LASTEXITCODE

    $Duration = [math]::Round(((Get-Date) - $StartTime).TotalSeconds, 1)

    if ($ExitCode -eq 0) {
        Write-Log "OK: El Niño pipeline complete (duration=$Duration s)"
    } else {
        Write-Log "WARN: El Niño pipeline exit=$ExitCode (duration=$Duration s)"
    }

    if ($Result) {
        foreach ($line in $Result) { Add-Content -Path $LogFile -Value $line -Encoding UTF8 }
    }

    # Check for generated report
    $DateStr = Get-Date -Format "yyyy-MM-dd"
    $ExpectedReport = Join-Path $ProjectRoot "reports\el-nino\$DateStr-el-nino-monitor.md"
    if (Test-Path $ExpectedReport) {
        Write-Log "OK: report generated: $ExpectedReport"
    } else {
        Write-Log "WARN: report not found at $ExpectedReport"
        # Try to find latest report
        $Reports = Get-ChildItem (Join-Path $ProjectRoot "reports\el-nino") -Filter "*-el-nino-monitor.md" -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending
        if ($Reports) {
            Write-Log "INFO: latest report: $($Reports[0].FullName)"
        }
    }

} catch {
    $Duration = [math]::Round(((Get-Date) - $StartTime).TotalSeconds, 1)
    Write-Log "ERROR: El Niño pipeline failed: $($_.Exception.Message) (duration=$Duration s)"
}

Write-Log "=== El Niño pipeline end ==="
exit 0