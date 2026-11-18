<#
.SYNOPSIS
  my-bot pre-market morning brief
.DESCRIPTION
  Triggered by Windows Task Scheduler at 08:30 on trading days.
  Runs Claude Code headless (-p) to execute morning brief + premarket checklist.
  Logs: logs/cron/YYYY-MM-DD-morning.log
  Report: reports/daily/YYYY-MM-DD-morning-brief.md
#>

# STARTUP MARKER
try {
    New-Item -ItemType Directory -Force -Path D:\AgentAlpha\my-bot\logs\cron -ErrorAction Stop | Out-Null
    "STARTUP $(Get-Date -Format 'o') PID=$PID" | Out-File -FilePath D:\AgentAlpha\my-bot\logs\cron\startup-morning.txt -Encoding utf8 -Append
} catch {}

$ErrorActionPreference = "Stop"

# STEP1: after ErrorActionPreference
try { "STEP1 $(Get-Date -Format 'o')" | Out-File -FilePath D:\AgentAlpha\my-bot\logs\cron\startup-morning.txt -Encoding utf8 -Append } catch {}

$ProjectRoot      = "d:\AgentAlpha\my-bot"
$PromptFile       = Join-Path $ProjectRoot "scripts\cron-morning-brief.md"
$LogDir           = Join-Path $ProjectRoot "logs\cron"
$TradingCalendar  = Join-Path $ProjectRoot "src\data\trading-calendar-2026.json"

$env:CLAUDE_CODE_GIT_BASH_PATH = "D:\Git\usr\bin\bash.exe"

# STEP2: before settings
try { "STEP2 $(Get-Date -Format 'o')" | Out-File -FilePath D:\AgentAlpha\my-bot\logs\cron\startup-morning.txt -Encoding utf8 -Append } catch {}

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
try { "STEP3 $(Get-Date -Format 'o')" | Out-File -FilePath D:\AgentAlpha\my-bot\logs\cron\startup-morning.txt -Encoding utf8 -Append } catch {}

# ─── Logging ──────────────────────────────────────────────────────────────
$DateStr = Get-Date -Format "yyyy-MM-dd"
$LogFile = Join-Path $LogDir "$DateStr-morning.log"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

# STEP4: after logging setup
try { "STEP4 $(Get-Date -Format 'o')" | Out-File -FilePath D:\AgentAlpha\my-bot\logs\cron\startup-morning.txt -Encoding utf8 -Append } catch {}

function Write-Log {
    param([string]$Message)
    $Timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $Line = "$Timestamp $Message"
    Add-Content -Path $LogFile -Value $Line -Encoding UTF8
    Write-Host $Line
}

Write-Log "=== my-bot morning brief start ==="

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

if (-not (Test-Path $PromptFile)) {
    Write-Log "ERROR: prompt file not found: $PromptFile"
    exit 1
}

# ─── Signal freshness check ─────────────────────────────────────────────────
# SignalFactory runs at 16:00 previous trading day. Accept any alpha_signals.json
# generated since yesterday morning (allows for re-runs and manual triggers).
$AlphaSignals = Join-Path $ProjectRoot "data\output\alpha_signals.json"
$YesterdayMorning = (Get-Date).Date.AddDays(-1)  # midnight yesterday
$StaleThreshold = (Get-Date).Date.AddDays(-4)    # 4 calendar days = stale

if (Test-Path $AlphaSignals) {
    $FileTime = (Get-Item $AlphaSignals).LastWriteTime
    $FileAgeDays = [math]::Round(((Get-Date) - $FileTime).TotalDays, 1)
    if ($FileTime -ge $YesterdayMorning) {
        Write-Log "OK: alpha_signals.json is fresh (age=${FileAgeDays}d, $($FileTime.ToString('yyyy-MM-dd HH:mm:ss')))"
    } elseif ($FileTime -ge $StaleThreshold) {
        Write-Log "WARN: alpha_signals.json may be stale (age=${FileAgeDays}d), will use but mark DEGRADED"
    } else {
        Write-Log "WARN: alpha_signals.json is very stale (age=${FileAgeDays}d), proceeding with available data"
    }
} else {
    Write-Log "WARN: alpha_signals.json not found, will proceed without ML alpha"
}

# ─── ETF Daily Data ──────────────────────────────────────────────────────
# Fetch 11-class ETF daily performance data for Layer 0 tracking
Write-Log "ETF: fetching daily performance data..."

$FetchETFScript = Join-Path $ProjectRoot "scripts\fetch_etf_daily.py"

if (Test-Path $FetchETFScript) {
    try {
        $ETFResult = & python $FetchETFScript 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Log "ETF: daily data fetched successfully"
        } else {
            Write-Log "ETF: fetch returned exit code $LASTEXITCODE (non-fatal)"
        }
    } catch {
        Write-Log "ETF: fetch failed (non-fatal): $($_.Exception.Message)"
    }
} else {
    Write-Log "ETF: fetch script not found, skipping"
}

# ─── Sector Ranking Data ─────────────────────────────────────────────────
# Fetch 31 Shenwan sector daily performance ranking for Layer 1 tracking
# Morning brief: use previous close data (T-1). If today is Monday, data
# will be from Friday — that's expected and not a degradation.
Write-Log "SECTOR: fetching daily ranking data..."

$FetchSectorScript = Join-Path $ProjectRoot "scripts\fetch_sector_ranking.py"

if (Test-Path $FetchSectorScript) {
    try {
        $SectorResult = & python $FetchSectorScript 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Log "SECTOR: daily ranking fetched successfully"
        } else {
            Write-Log "SECTOR: fetch returned exit code $LASTEXITCODE (non-fatal)"
            if ($SectorResult) {
                foreach ($line in $SectorResult) {
                    Add-Content -Path $LogFile -Value "[sector] $line" -Encoding UTF8
                }
            }
        }
    } catch {
        Write-Log "SECTOR: fetch failed (non-fatal): $($_.Exception.Message)"
    }
} else {
    Write-Log "SECTOR: fetch script not found, skipping"
}

# ─── HK Stock Data ────────────────────────────────────────────────────────
# Fetch HK watchlist quotes via akshare → Wind MCP fallback
Write-Log "HK: fetching stock data..."

$HKStockScript = Join-Path $ProjectRoot "scripts\hk_stock_data.py"

if (Test-Path $HKStockScript) {
    try {
        $HKResult = & python $HKStockScript 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Log "HK: stock data fetched successfully"
        } else {
            Write-Log "HK: fetch returned exit code $LASTEXITCODE (non-fatal)"
            if ($HKResult) {
                foreach ($line in $HKResult) {
                    Add-Content -Path $LogFile -Value "[hk] $line" -Encoding UTF8
                }
            }
        }
    } catch {
        Write-Log "HK: fetch failed (non-fatal): $($_.Exception.Message)"
    }
} else {
    Write-Log "HK: script not found, skipping"
}

# ─── Build Report Context ────────────────────────────────────────────────
# Regenerate Layer 0/1 summaries from freshly-fetched ETF and sector data.
# Also generate CSI300 daily K-line data (v4.3.1 — P0 fix for data integrity).
# Runs AFTER all data fetches so summaries use the latest data.
Write-Log "CONTEXT: building report context..."

$BuildContextScript = Join-Path $ProjectRoot "scripts\build_report_context.py"

if (Test-Path $BuildContextScript) {
    try {
        $ContextResult = & python $BuildContextScript 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Log "CONTEXT: report context built successfully"
            if ($ContextResult) {
                foreach ($line in $ContextResult) {
                    Add-Content -Path $LogFile -Value "[context] $line" -Encoding UTF8
                }
            }
        } else {
            Write-Log "CONTEXT: build returned exit code $LASTEXITCODE (non-fatal)"
        }
    } catch {
        Write-Log "CONTEXT: build failed (non-fatal): $($_.Exception.Message)"
    }
} else {
    Write-Log "CONTEXT: script not found, skipping"
}

# Verify CSI300 data file exists and is fresh
$CSI300Daily = Join-Path $ProjectRoot "data\output\_csi300_daily.json"
if (Test-Path $CSI300Daily) {
    try {
        $CSI300Data = Get-Content $CSI300Daily -Raw -Encoding UTF8 | ConvertFrom-Json
        $LatestDate = $CSI300Data.latest.date
        $LatestClose = $CSI300Data.latest.close
        Write-Log "CSI300: _csi300_daily.json date=$LatestDate close=$LatestClose"
    } catch {
        Write-Log "CSI300: _csi300_daily.json parse failed"
    }
} else {
    Write-Log "CSI300: WARN _csi300_daily.json not found — market data will be DEGRADED"
}

# ─── Execute ──────────────────────────────────────────────────────────────
Write-Log "START: executing morning brief"

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
<!-- INJECTED BY run-morning-brief.ps1: authoritative date information -->
当前日期: ${TodayStr}（${TodayDow}）
星期: $TodayDow
是否为周末: $($(if ([int]$TodayDate.DayOfWeek -in 0,6) {'yes'} else {'no'}))
是否为交易日（按交易日历判断）: 是（PowerShell已通过周末检查+交易日历closed_dates检查）
请在所有日期标注中使用此信息，严禁自行推算星期几。
<!-- END INJECTED DATE CONTEXT -->

"@

$PromptContent = Get-Content $PromptFile -Raw
$FullPrompt = $DateContext + $PromptContent
$StartTime = Get-Date
$ExitCode = 0

try {
    # Session 0 fix: claude -p defaults to C:\WINDOWS\system32, must cd to project
    Set-Location $ProjectRoot
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
Write-Log "END: morning brief finished (exit=$ExitCode, duration=$Duration min)"

if ($Output) {
    foreach ($line in $Output) { Add-Content -Path $LogFile -Value $line -Encoding UTF8 }
}
Write-Log "=== log saved: $LogFile ==="

$ReportFile = Join-Path $ProjectRoot "reports\daily\$DateStr-morning-brief.md"
if (Test-Path $ReportFile) {
    Write-Log "OK: report generated: $ReportFile"
    $ReportContent = Get-Content $ReportFile -Raw
    if ($ReportContent -match "MORNING_BRIEF_COMPLETE") {
        Write-Log "OK: MORNING_BRIEF_COMPLETE confirmed"
    } else {
        Write-Log "WARN: report missing completion marker"
    }
} else {
    Write-Log "WARN: report not found: $ReportFile"
}

Write-Log "=== my-bot morning brief end ==="
exit $ExitCode