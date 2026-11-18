<#
.SYNOPSIS
  信号工厂每日盘前自动化
.DESCRIPTION
  每天盘前（8:00）自动运行，为新一天交易提供 ML alpha 信号参考。
  流程: 数据更新 -> 信号生成 -> 同步到 FM_trade
  日志: signal-factory/log/cron/YYYY-MM-DD-pipeline.log
#>

# STARTUP MARKER
try {
    New-Item -ItemType Directory -Force -Path D:\AgentAlpha\signal-factory\log\cron -ErrorAction Stop | Out-Null
    "STARTUP $(Get-Date -Format 'o') PID=$PID" | Out-File -FilePath D:\AgentAlpha\signal-factory\log\cron\startup.txt -Encoding utf8 -Append
} catch {}

$ErrorActionPreference = "Stop"

# Fix console encoding for emoji/log output
[Console]::OutputEncoding = [Text.Encoding]::UTF8

# ─── Configuration ────────────────────────────────────────────────────────
$ProjectRoot      = "D:\AgentAlpha\signal-factory"
$PipelineScript   = Join-Path $ProjectRoot "scripts\daily_pipeline.py"
$PythonExe        = "C:\Python312\python.exe"
$LogDir           = Join-Path $ProjectRoot "log\cron"
$TradingCalendar  = "D:\AgentAlpha\my-bot\src\data\trading-calendar-2026.json"

# ─── Logging ──────────────────────────────────────────────────────────────
$DateStr = Get-Date -Format "yyyy-MM-dd"
$LogFile = Join-Path $LogDir "$DateStr-pipeline.log"
New-Item -ItemType Directory -Force -Path $LogDir -ErrorAction SilentlyContinue | Out-Null

function Write-Log {
    param([string]$Message)
    $Timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $Line = "$Timestamp $Message"
    try {
        Add-Content -Path $LogFile -Value $Line -ErrorAction SilentlyContinue
    } catch {}
    Write-Host $Line
}

Write-Log "=== Signal Factory daily pipeline start ==="

# ─── Trading day check ────────────────────────────────────────────────────
$DayOfWeek = (Get-Date).DayOfWeek
if (($DayOfWeek -eq [System.DayOfWeek]::Saturday) -or ($DayOfWeek -eq [System.DayOfWeek]::Sunday)) {
    Write-Log "SKIP: weekend, not a trading day"
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
    } catch {
        Write-Log "WARN: trading calendar read failed, assuming trading day"
    }
}

if (-not $IsTradingDay) {
    exit 0
}

# ─── Pre-flight checks ────────────────────────────────────────────────────
if (-not (Test-Path $PipelineScript)) {
    Write-Log "ERROR: pipeline script not found: $PipelineScript"
    exit 1
}

if (-not (Test-Path $PythonExe)) {
    Write-Log "ERROR: Python not found: $PythonExe"
    exit 1
}

# ─── Execute pipeline ─────────────────────────────────────────────────────
Write-Log "START: executing signal factory pipeline"

$StartTime = Get-Date
$ExitCode = 0

try {
    Set-Location $ProjectRoot
    $Result = & $PythonExe $PipelineScript --skip-update 2>&1
    $ExitCode = $LASTEXITCODE

    # Write output to log
    if ($Result) {
        foreach ($line in $Result) {
            try {
                Add-Content -Path $LogFile -Value $line -ErrorAction SilentlyContinue
            } catch {}
        }
    }
} catch {
    Write-Log "ERROR: pipeline execution exception: $($_.Exception.Message)"
    $ExitCode = -1
}

$Duration = [math]::Round(((Get-Date) - $StartTime).TotalMinutes, 1)
Write-Log "END: pipeline finished (exit=$ExitCode, duration=$Duration min)"

# ─── Verify output ────────────────────────────────────────────────────────
$AlphaOutput = Join-Path $ProjectRoot "data\output\alpha_signals.json"
if (Test-Path $AlphaOutput) {
    try {
        $Alpha = Get-Content $AlphaOutput -Raw -Encoding UTF8 | ConvertFrom-Json
        Write-Log "OK: signals generated - $($Alpha.total_stocks) stocks, model: $($Alpha.model), date: $($Alpha.date)"
    } catch {
        Write-Log "WARN: signal file exists but cannot parse"
    }
} else {
    Write-Log "ERROR: signal file not generated: $AlphaOutput"
}

$FmSignals = "D:\AgentAlpha\my-bot\data\output\alpha_signals.json"
if (Test-Path $FmSignals) {
    Write-Log "OK: FM_trade sync complete"
} else {
    Write-Log "WARN: FM_trade sync file not found (sync step may have failed)"
}

Write-Log "=== Signal Factory daily pipeline end ==="
exit $ExitCode