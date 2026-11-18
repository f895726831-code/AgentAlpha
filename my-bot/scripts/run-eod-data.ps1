<#
.SYNOPSIS
  my-bot end-of-day data refresh
.DESCRIPTION
  Runs after market close (15:30 CST) on trading days to refresh ETF daily
  data and generate CSI300 daily K-line JSON. This eliminates the weekend
  data vacuum that caused the 2026-10-26 morning brief data error.

  Schedule via Windows Task Scheduler:
    Trigger: Daily at 15:30, only on trading days
    Action: powershell.exe -ExecutionPolicy Bypass -File "D:\AgentAlpha\my-bot\scripts\run-eod-data.ps1"

  Logs: logs/cron/YYYY-MM-DD-eod.log
  Outputs:
    - data/output/_etf_daily.json (refreshed)
    - data/output/_csi300_daily.json (regenerated)
#>

$ErrorActionPreference = "Stop"

$ProjectRoot      = "d:\AgentAlpha\my-bot"
$LogDir           = Join-Path $ProjectRoot "logs\cron"
$ScriptDir        = Join-Path $ProjectRoot "scripts"
$TradingCalendar  = Join-Path $ProjectRoot "src\data\trading-calendar-2026.json"

$DateStr = Get-Date -Format "yyyy-MM-dd"
$LogFile = Join-Path $LogDir "$DateStr-eod.log"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Write-Log {
    param([string]$Message)
    $Timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $Line = "$Timestamp $Message"
    Add-Content -Path $LogFile -Value $Line -Encoding UTF8
    Write-Host $Line
}

Write-Log "=== my-bot EOD data refresh start ==="

# ─── Trading day check ────────────────────────────────────────────────────
$DayOfWeek = (Get-Date).DayOfWeek
if (($DayOfWeek -eq [System.DayOfWeek]::Saturday) -or ($DayOfWeek -eq [System.DayOfWeek]::Sunday)) {
    Write-Log "SKIP: weekend"
    exit 0
}

$IsTradingDay = $true
if (Test-Path $TradingCalendar) {
    try {
        $Calendar = Get-Content $TradingCalendar -Raw -Encoding UTF8 | ConvertFrom-Json
        $TodayStr = Get-Date -Format "yyyy-MM-dd"
        if ($Calendar.closed_dates -contains $TodayStr) {
            Write-Log "SKIP: holiday ($TodayStr)"
            $IsTradingDay = $false
        }
        $MakeupDates = @()
        foreach ($item in $Calendar.makeup_trading_days) {
            if ($item -is [string]) { $MakeupDates += $item }
            elseif ($item.date) { $MakeupDates += $item.date }
        }
        if ($Calendar.special_trading_dates) { $MakeupDates += $Calendar.special_trading_dates }
        if ($MakeupDates -contains $TodayStr) { $IsTradingDay = $true }
    } catch {
        Write-Log "WARN: calendar read failed, assuming trading day"
    }
}
if (-not $IsTradingDay) { exit 0 }

# ─── Step 1: ETF Daily Data ───────────────────────────────────────────────
Write-Log "STEP1: fetching ETF daily data..."
$FetchETFScript = Join-Path $ScriptDir "fetch_etf_daily.py"

if (Test-Path $FetchETFScript) {
    try {
        $ETFResult = & python -I $FetchETFScript 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Log "OK: ETF daily data fetched"
            if ($ETFResult) {
                foreach ($line in $ETFResult) {
                    Add-Content -Path $LogFile -Value "[etf] $line" -Encoding UTF8
                }
            }
        } else {
            Write-Log "ERROR: ETF fetch returned exit code $LASTEXITCODE"
            if ($ETFResult) {
                foreach ($line in $ETFResult) {
                    Add-Content -Path $LogFile -Value "[etf-err] $line" -Encoding UTF8
                }
            }
        }
    } catch {
        Write-Log "ERROR: ETF fetch exception: $($_.Exception.Message)"
    }
} else {
    Write-Log "WARN: fetch_etf_daily.py not found, skipping"
}

# ─── Step 2: CSI300 Daily K-line ──────────────────────────────────────────
Write-Log "STEP2: generating CSI300 daily data..."
$BuildContextScript = Join-Path $ScriptDir "build_report_context.py"

if (Test-Path $BuildContextScript) {
    try {
        $CSI300Result = & python -I $BuildContextScript --eod-light 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Log "OK: CSI300 daily data generated"
            if ($CSI300Result) {
                foreach ($line in $CSI300Result) {
                    Add-Content -Path $LogFile -Value "[csi300] $line" -Encoding UTF8
                }
            }
        } else {
            Write-Log "ERROR: CSI300 generation returned exit code $LASTEXITCODE"
            if ($CSI300Result) {
                foreach ($line in $CSI300Result) {
                    Add-Content -Path $LogFile -Value "[csi300-err] $line" -Encoding UTF8
                }
            }
        }
    } catch {
        Write-Log "ERROR: CSI300 generation exception: $($_.Exception.Message)"
    }
} else {
    Write-Log "WARN: build_report_context.py not found, skipping"
}

# ─── Step 3: Verify outputs ───────────────────────────────────────────────
Write-Log "STEP3: verifying outputs..."

$ETFFile = Join-Path $ProjectRoot "data\output\_etf_daily.json"
if (Test-Path $ETFFile) {
    $ETFTime = (Get-Item $ETFFile).LastWriteTime
    Write-Log "OK: _etf_daily.json last modified $($ETFTime.ToString('yyyy-MM-dd HH:mm:ss'))"
} else {
    Write-Log "WARN: _etf_daily.json not found"
}

$CSI300File = Join-Path $ProjectRoot "data\output\_csi300_daily.json"
if (Test-Path $CSI300File) {
    $CSI300Time = (Get-Item $CSI300File).LastWriteTime
    try {
        $CSI300Data = Get-Content $CSI300File -Raw -Encoding UTF8 | ConvertFrom-Json
        $Latest = $CSI300Data.latest
        $Direction = if ($Latest.chg_pct -gt 0) { "UP" } elseif ($Latest.chg_pct -lt 0) { "DOWN" } else { "FLAT" }
        Write-Log "OK: _csi300_daily.json date=$($Latest.date) close=$($Latest.close) chg=$($Latest.chg_pct)% dir=$Direction"
    } catch {
        Write-Log "WARN: _csi300_daily.json parse failed: $($_.Exception.Message)"
    }
} else {
    Write-Log "ERROR: _csi300_daily.json not found"
}

Write-Log "=== my-bot EOD data refresh end ==="