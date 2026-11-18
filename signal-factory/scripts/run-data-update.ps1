<#
.SYNOPSIS
  信号工厂收盘后数据更新
.DESCRIPTION
  每天收盘后（15:30）运行，下载当日增量数据并转为 Qlib bin 格式。
  日志: signal-factory/log/cron/YYYY-MM-DD-data-update.log
#>

# STARTUP MARKER
try {
    New-Item -ItemType Directory -Force -Path D:\AgentAlpha\signal-factory\log\cron -ErrorAction Stop | Out-Null
    "STARTUP $(Get-Date -Format 'o') PID=$PID" | Out-File -FilePath D:\AgentAlpha\signal-factory\log\cron\startup-data-update.txt -Encoding utf8 -Append
} catch {}

$ErrorActionPreference = "Stop"

# ─── Configuration ────────────────────────────────────────────────────────
$ProjectRoot      = "D:\AgentAlpha\signal-factory"
$UpdateScript     = Join-Path $ProjectRoot "scripts\update_daily.py"
$PythonExe        = "C:\Python312\python.exe"
$LogDir           = Join-Path $ProjectRoot "log\cron"
$TradingCalendar  = "D:\AgentAlpha\my-bot\src\data\trading-calendar-2026.json"

# ─── Logging ──────────────────────────────────────────────────────────────
$DateStr = Get-Date -Format "yyyy-MM-dd"
$LogFile = Join-Path $LogDir "$DateStr-data-update.log"
New-Item -ItemType Directory -Force -Path $LogDir -ErrorAction SilentlyContinue | Out-Null

function Write-Log {
    param([string]$Message)
    $Timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $Line = "$Timestamp $Message"
    try { Add-Content -Path $LogFile -Value $Line -ErrorAction SilentlyContinue } catch {}
    Write-Host $Line
}

Write-Log "=== Data update start ==="

# ─── Trading day check ────────────────────────────────────────────────────
$DayOfWeek = (Get-Date).DayOfWeek
if (($DayOfWeek -eq [System.DayOfWeek]::Saturday) -or ($DayOfWeek -eq [System.DayOfWeek]::Sunday)) {
    Write-Log "SKIP: weekend"
    exit 0
}

if (Test-Path $TradingCalendar) {
    try {
        $Calendar = Get-Content $TradingCalendar -Raw -Encoding UTF8 | ConvertFrom-Json
        $TodayStr = Get-Date -Format "yyyy-MM-dd"
        if ($Calendar.closed_dates -contains $TodayStr) {
            Write-Log "SKIP: holiday ($TodayStr)"
            exit 0
        }
    } catch {
        Write-Log "WARN: calendar read failed, assuming trading day"
    }
}

# ─── Pre-flight checks ────────────────────────────────────────────────────
if (-not (Test-Path $UpdateScript)) {
    Write-Log "ERROR: update script not found: $UpdateScript"
    exit 1
}

# ─── Execute data update ──────────────────────────────────────────────────
Write-Log "START: downloading today's data"

$StartTime = Get-Date
$ExitCode = 0

try {
    Set-Location $ProjectRoot
    $Result = & $PythonExe $UpdateScript 2>&1
    $ExitCode = $LASTEXITCODE

    if ($Result) {
        foreach ($line in $Result) {
            try { Add-Content -Path $LogFile -Value $line -ErrorAction SilentlyContinue } catch {}
        }
    }
} catch {
    Write-Log "ERROR: $($_.Exception.Message)"
    $ExitCode = -1
}

$Duration = [math]::Round(((Get-Date) - $StartTime).TotalMinutes, 1)
Write-Log "END: data update finished (exit=$ExitCode, duration=$Duration min)"
Write-Log "=== Data update end ==="
exit $ExitCode