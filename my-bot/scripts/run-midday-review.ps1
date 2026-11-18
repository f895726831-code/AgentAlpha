<#
.SYNOPSIS
  my-bot midday market review
.DESCRIPTION
  Triggered by Windows Task Scheduler at 11:30 on trading days.
  Runs Claude Code headless (-p) to execute midday review + position check.
  Logs: logs/cron/YYYY-MM-DD-midday.log
  Report: reports/daily/YYYY-MM-DD-midday.md
#>

# STARTUP MARKER
try {
    New-Item -ItemType Directory -Force -Path D:\AgentAlpha\my-bot\logs\cron -ErrorAction Stop | Out-Null
    "STARTUP $(Get-Date -Format 'o') PID=$PID" | Out-File -FilePath D:\AgentAlpha\my-bot\logs\cron\startup-midday.txt -Encoding utf8 -Append
} catch {}

$ErrorActionPreference = "Stop"

# STEP1: after ErrorActionPreference
try { "STEP1 $(Get-Date -Format 'o')" | Out-File -FilePath D:\AgentAlpha\my-bot\logs\cron\startup-midday.txt -Encoding utf8 -Append } catch {}

$ProjectRoot      = "d:\AgentAlpha\my-bot"
$PromptFile       = Join-Path $ProjectRoot "scripts\cron-midday-review.md"
$LogDir           = Join-Path $ProjectRoot "logs\cron"
$TradingCalendar  = Join-Path $ProjectRoot "src\data\trading-calendar-2026.json"

$env:CLAUDE_CODE_GIT_BASH_PATH = "D:\Git\usr\bin\bash.exe"

# STEP2: before settings
try { "STEP2 $(Get-Date -Format 'o')" | Out-File -FilePath D:\AgentAlpha\my-bot\logs\cron\startup-midday.txt -Encoding utf8 -Append } catch {}

$SettingsLocal = Join-Path $ProjectRoot "..\.claude\settings.local.json"
if (Test-Path $SettingsLocal) {
    try {
        $Settings = Get-Content $SettingsLocal -Raw -Encoding UTF8 | ConvertFrom-Json
        $env:ANTHROPIC_API_KEY    = $Settings.env.ANTHROPIC_AUTH_TOKEN
        $env:ANTHROPIC_BASE_URL   = $Settings.env.ANTHROPIC_BASE_URL
        $env:ANTHROPIC_MODEL      = $Settings.env.ANTHROPIC_MODEL
        $env:ANTHROPIC_SMALL_FAST_MODEL     = $Settings.env.ANTHROPIC_SMALL_FAST_MODEL
        $env:ANTHROPIC_DEFAULT_HAIKU_MODEL  = $Settings.env.ANTHROPIC_DEFAULT_HAIKU_MODEL
        $env:ANTHROPIC_DEFAULT_SONNET_MODEL = $Settings.env.ANTHROPIC_DEFAULT_SONNET_MODEL
        $env:ANTHROPIC_DEFAULT_OPUS_MODEL   = $Settings.env.ANTHROPIC_DEFAULT_OPUS_MODEL
        $env:CLAUDE_CODE_SUBAGENT_MODEL     = $Settings.env.CLAUDE_CODE_SUBAGENT_MODEL
    } catch { }
}

# STEP3: after settings
try { "STEP3 $(Get-Date -Format 'o')" | Out-File -FilePath D:\AgentAlpha\my-bot\logs\cron\startup-midday.txt -Encoding utf8 -Append } catch {}

# ─── Logging ──────────────────────────────────────────────────────────────
$DateStr = Get-Date -Format "yyyy-MM-dd"
$LogFile = Join-Path $LogDir "$DateStr-midday.log"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

# STEP4: after logging setup
try { "STEP4 $(Get-Date -Format 'o')" | Out-File -FilePath D:\AgentAlpha\my-bot\logs\cron\startup-midday.txt -Encoding utf8 -Append } catch {}

function Write-Log {
    param([string]$Message)
    $Timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $Line = "$Timestamp $Message"
    Add-Content -Path $LogFile -Value $Line -Encoding UTF8
    Write-Host $Line
}

Write-Log "=== my-bot midday review start ==="

# Session 0 fix: Windows Task Scheduler defaults CWD to C:\WINDOWS\system32
# Must cd to project root BEFORE any relative-path scripts
Set-Location $ProjectRoot

# ─── Pre-step: Refresh HK stock data (akshare → Wind MCP dual channel) ──
try {
    Write-Log "PRE: refresh HK stock data"
    $hkStart = Get-Date
    $hkResult = python scripts/hk_stock_data.py 2>&1
    $hkExit = $LASTEXITCODE
    $hkDuration = [math]::Round(((Get-Date) - $hkStart).TotalSeconds, 1)
    if ($hkExit -eq 0) {
        Write-Log "  OK: HK stock data ($hkDuration s)"
    } else {
        Write-Log "  WARN: HK stock data exit=$hkExit ($hkDuration s)"
    }
} catch {
    Write-Log "  WARN: HK stock data refresh failed: $_"
}

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

# ─── TiMi Midday Re-run ──────────────────────────────────────────────────
# Re-run TiMi with intraday prices to update signals for afternoon session.
# Uses akshare real-time spot quotes (fallback: use yesterday's data unchanged).
Write-Log "TIMI-MIDDAY: re-running TiMi with intraday prices..."

$TiMiMiddayScript = Join-Path $ProjectRoot "scripts\timi_midday.py"

if (Test-Path $TiMiMiddayScript) {
    try {
        $TiMiMiddayResult = & python $TiMiMiddayScript 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Log "  OK: TiMi midday signals generated"
        } else {
            Write-Log "  WARN: TiMi midday exit=$LASTEXITCODE (non-fatal)"
        }
        if ($TiMiMiddayResult) {
            foreach ($line in $TiMiMiddayResult) {
                Add-Content -Path $LogFile -Value "[timi-midday] $line" -Encoding UTF8
            }
        }

        # Ingest midday signals into signal-store
        try {
            $TiMiMiddayIngest = & python scripts/ingest_timi_signals.py --source midday 2>&1
            if ($LASTEXITCODE -eq 0) {
                Write-Log "  OK: midday signals ingested to signal-store"
            }
        } catch {
            Write-Log "  WARN: midday ingest failed (non-fatal): $($_.Exception.Message)"
        }
    } catch {
        Write-Log "  WARN: TiMi midday failed (non-fatal): $($_.Exception.Message)"
    }
} else {
    Write-Log "  SKIP: timi_midday.py not found"
}

if (-not (Test-Path $PromptFile)) {
    Write-Log "ERROR: prompt file not found: $PromptFile"
    exit 1
}

# ─── Pre-check: morning brief existence ───────────────────────────────────
$MorningBriefFile = Join-Path $ProjectRoot "reports\daily\$DateStr-morning-brief.md"
if (Test-Path $MorningBriefFile) {
    Write-Log "PRE-CHECK: morning brief found: $MorningBriefFile"
} else {
    Write-Log "WARN: morning brief NOT found: $MorningBriefFile"
    Write-Log "WARN: midday report will mark 晨报 as DEGRADED"
}

# ─── Execute ──────────────────────────────────────────────────────────────
Write-Log "START: executing midday review"

# Build explicit date context to prevent LLM day-of-week hallucination.
# The model has no reliable internal calendar for 2026; we must tell it the
# correct date and weekday in Chinese.
$TodayDate = Get-Date
$ChineseDays = @{
    "Sunday"    = "周日"
    "Monday"    = "周一"
    "Tuesday"   = "周二"
    "Wednesday" = "周三"
    "Thursday"  = "周四"
    "Friday"    = "周五"
    "Saturday"  = "周六"
}
$TodayDow = $ChineseDays[$TodayDate.DayOfWeek.ToString()]
$TodayStr = $TodayDate.ToString("yyyy-MM-dd")
$DateContext = @"
<!-- INJECTED BY run-midday-review.ps1: authoritative date information -->
当前日期: ${TodayStr}（${TodayDow}）
星期: $TodayDow
请在所有日期标注中使用此信息，严禁自行推算星期几。
<!-- END INJECTED DATE CONTEXT -->

"@

$PromptContent = Get-Content $PromptFile -Raw
$FullPrompt = $DateContext + $PromptContent
$StartTime = Get-Date
$ExitCode = 0

try {
    # Fix: ensure UTF-8 encoding when piping to native commands (Session 0 defaults to ASCII)
    $OutputEncoding = [Text.Encoding]::UTF8
    [Console]::OutputEncoding = [Text.Encoding]::UTF8
    $Output = $FullPrompt | claude -p 2>&1
    $ExitCode = $LASTEXITCODE
} catch {
    Write-Log "ERROR: Claude Code exception: $($_.Exception.Message)"
    $ExitCode = -1
}

$Duration = [math]::Round(((Get-Date) - $StartTime).TotalMinutes, 1)
Write-Log "END: midday review finished (exit=$ExitCode, duration=$Duration min)"

if ($Output) {
    foreach ($line in $Output) { Add-Content -Path $LogFile -Value $line -Encoding UTF8 }
}
Write-Log "=== log saved: $LogFile ==="

$ReportFile = Join-Path $ProjectRoot "reports\daily\$DateStr-midday.md"
if (Test-Path $ReportFile) {
    Write-Log "OK: report generated: $ReportFile"
    $ReportContent = Get-Content $ReportFile -Raw
    if ($ReportContent -match "MIDDAY_COMPLETE") {
        Write-Log "OK: MIDDAY_COMPLETE confirmed"
    } else {
        Write-Log "WARN: report missing completion marker"
    }
} else {
    Write-Log "WARN: report not found: $ReportFile"
}

Write-Log "=== my-bot midday review end ==="
exit $ExitCode