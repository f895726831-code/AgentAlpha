<#
.SYNOPSIS
  my-bot daily after-close watchlist scan
.DESCRIPTION
  Triggered by Windows Task Scheduler at 16:05 on trading days.
  Runs Claude Code headless (-p) to execute the five-level funnel scan.
  Logs: logs/cron/YYYY-MM-DD-scan.log
  Report: reports/daily/YYYY-MM-DD-watchlist-scan.md
#>

# STARTUP MARKER
try {
    New-Item -ItemType Directory -Force -Path D:\AgentAlpha\my-bot\logs\cron -ErrorAction Stop | Out-Null
    "STARTUP $(Get-Date -Format 'o') PID=$PID" | Out-File -FilePath D:\AgentAlpha\my-bot\logs\cron\startup-daily.txt -Encoding utf8 -Append
} catch {}

$ErrorActionPreference = "Stop"

# STEP1: after ErrorActionPreference
try { "STEP1 $(Get-Date -Format 'o')" | Out-File -FilePath D:\AgentAlpha\my-bot\logs\cron\startup-daily.txt -Encoding utf8 -Append } catch {}

# ─── Configuration ────────────────────────────────────────────────────────
$ProjectRoot      = "d:\AgentAlpha\my-bot"
$PromptFile       = Join-Path $ProjectRoot "scripts\cron-daily-scan.md"
$LogDir           = Join-Path $ProjectRoot "logs\cron"
$TradingCalendar  = Join-Path $ProjectRoot "src\data\trading-calendar-2026.json"

# Claude Code on Windows requires git-bash
$env:CLAUDE_CODE_GIT_BASH_PATH = "D:\Git\usr\bin\bash.exe"

# STEP2: before settings
try { "STEP2 $(Get-Date -Format 'o')" | Out-File -FilePath D:\AgentAlpha\my-bot\logs\cron\startup-daily.txt -Encoding utf8 -Append } catch {}

# ─── Claude Code Auth (from settings.local.json) ──────────────────────────
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
    } catch {
        # Will be caught by pre-flight check below
    }
}

# STEP3: after settings
try { "STEP3 $(Get-Date -Format 'o')" | Out-File -FilePath D:\AgentAlpha\my-bot\logs\cron\startup-daily.txt -Encoding utf8 -Append } catch {}

# ─── Logging ──────────────────────────────────────────────────────────────
$DateStr = Get-Date -Format "yyyy-MM-dd"
$LogFile = Join-Path $LogDir "$DateStr-scan.log"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

# STEP4: after logging setup
try { "STEP4 $(Get-Date -Format 'o')" | Out-File -FilePath D:\AgentAlpha\my-bot\logs\cron\startup-daily.txt -Encoding utf8 -Append } catch {}

function Write-Log {
    param([string]$Message)
    $Timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $Line = "$Timestamp $Message"
    Add-Content -Path $LogFile -Value $Line -Encoding UTF8
    Write-Host $Line
}

Write-Log "=== my-bot daily scan start ==="

# ─── Signal freshness check ─────────────────────────────────────────────────
# SignalFactory runs at 16:00 and takes ~25 min. Accept any alpha_signals.json
# generated today (handles manual runs, cron runs, and re-runs).
# Use midnight today as threshold — any file written today is considered fresh.
$AlphaSignals = Join-Path $ProjectRoot "data\output\alpha_signals.json"
$TodayStart = (Get-Date).Date  # midnight today — matches any file from today
$MaxWait = 1800  # 30 minutes
$Waited = 0

while ($Waited -lt $MaxWait) {
    if (Test-Path $AlphaSignals) {
        $FileTime = (Get-Item $AlphaSignals).LastWriteTime
        if ($FileTime -ge $TodayStart) {
            Write-Log "OK: alpha_signals.json is fresh ($($FileTime.ToString('HH:mm:ss')))"
            break
        }
    }
    if ($Waited -eq 0) {
        Write-Log "WAIT: alpha_signals.json not yet updated by SignalFactory, waiting..."
    }
    Start-Sleep -Seconds 60
    $Waited += 60
}

if ($Waited -ge $MaxWait) {
    Write-Log "WARN: alpha_signals.json still stale after ${MaxWait}s, proceeding with available data"
}

# ─── Trading day check ────────────────────────────────────────────────────
# 1) Skip weekends
$DayOfWeek = (Get-Date).DayOfWeek
if (($DayOfWeek -eq [System.DayOfWeek]::Saturday) -or ($DayOfWeek -eq [System.DayOfWeek]::Sunday)) {
    Write-Log "SKIP: weekend, not a trading day"
    exit 0
}

# 2) Check trading calendar for holidays
$IsTradingDay = $true
$CalendarExists = Test-Path $TradingCalendar

if ($CalendarExists) {
    try {
        $CalendarJson = Get-Content $TradingCalendar -Raw -Encoding UTF8
        $Calendar = $CalendarJson | ConvertFrom-Json
        $TodayStr = Get-Date -Format "yyyy-MM-dd"
        $ClosedDates = $Calendar.closed_dates
        # Extract makeup dates (may be objects with .date property or plain strings)
        $MakeupDates = @()
        foreach ($item in $Calendar.makeup_trading_days) {
            if ($item -is [string]) {
                $MakeupDates += $item
            } elseif ($item.date) {
                $MakeupDates += $item.date
            }
        }

        # Also check special_trading_dates if present
        if ($Calendar.special_trading_dates) {
            $MakeupDates += $Calendar.special_trading_dates
        }

        # Check if today is a holiday
        if ($ClosedDates -contains $TodayStr) {
            Write-Log "SKIP: holiday ($TodayStr)"
            $IsTradingDay = $false
        }

        # Check if today is a makeup trading day (weekend but market open)
        if ($MakeupDates -contains $TodayStr) {
            Write-Log "INFO: makeup trading day, running"
            $IsTradingDay = $true
        }
    }
    catch {
        Write-Log "WARN: trading calendar read failed, assuming trading day"
        $IsTradingDay = $true
    }
}
else {
    Write-Log "WARN: trading calendar file not found, assuming trading day"
}

if (-not $IsTradingDay) {
    exit 0
}

# ─── Pre-flight checks ────────────────────────────────────────────────────
if (-not (Test-Path $PromptFile)) {
    Write-Log "ERROR: prompt file not found: $PromptFile"
    exit 1
}

Write-Log "CHECK: data source connectivity..."

try {
    $PingOk = Test-Connection -ComputerName "wind.wind.com.cn" -Count 1 -Quiet -ErrorAction SilentlyContinue
    if ($PingOk) {
        Write-Log "OK: Wind network reachable"
    }
    else {
        Write-Log "WARN: Wind server not reachable, will attempt anyway"
    }
}
catch {
    Write-Log "WARN: network check failed, will attempt anyway"
}

# ─── Qlib Data Update (akshare/baostock → CSV + bin) ────────────────────
# REMOVED (2026-11-03): redundant with 08:05 signal-factory-pipeline.ps1
# which already runs update_daily.py before generating alpha signals.
# The 16:05 daily scan does not consume Qlib CSV/bin data directly —
# timi_market_data.py fetches its own OHLCV independently.
Write-Log "QLIB-UPDATE: skipped (handled by 08:05 signal-factory-pipeline.ps1)"

$QlibConfigFile = Join-Path $ProjectRoot "scripts\qlib\config.json"

# ─── Qlib Data Sync (DISABLED 2026-11-03: dead code) ──────────────────
# sync_daily.py calls WindBridge.sync() with stale Wind JSON (201 files,
# last updated 2026-08-07). The Wind MCP → CSV flow has been fully replaced
# by update_daily.py (akshare/baostock → CSV + bin) at 08:05.
Write-Log "QLIB-SYNC: skipped (dead code — Wind JSON stale since 2026-08-07)"

# ─── Qlib Alpha Generation ────────────────────────────────────────────────
# Generate ML alpha signals for Stage 1 stock selection
Write-Log "QLIB: starting alpha generation..."

$QlibAlphaScript = Join-Path $ProjectRoot "scripts\qlib\run_alpha.py"

if (Test-Path $QlibAlphaScript) {
    try {
        $QlibConfig = Get-Content $QlibConfigFile -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($QlibConfig.qlib.enabled -and $QlibConfig.qlib.alpha.enabled) {
            $QlibAlphaResult = & $QlibPython $QlibAlphaScript 2>&1
            if ($LASTEXITCODE -eq 0) {
                Write-Log "QLIB: alpha generation completed successfully"
            } else {
                Write-Log "QLIB: alpha generation returned exit code $LASTEXITCODE (non-fatal)"
            }
            # Log Qlib alpha output
            if ($QlibAlphaResult) {
                foreach ($line in $QlibAlphaResult) {
                    Add-Content -Path $LogFile -Value "[qlib-alpha] $line" -Encoding UTF8
                }
            }
        } else {
            Write-Log "QLIB: alpha generation disabled in config, skipping"
        }
    } catch {
        Write-Log "QLIB: alpha generation failed (non-fatal): $($_.Exception.Message)"
    }
} else {
    Write-Log "QLIB: alpha script not found, skipping (Phase 2 not yet installed)"
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
Write-Log "SECTOR: fetching daily ranking data..."

$FetchSectorScript = Join-Path $ProjectRoot "scripts\fetch_sector_ranking.py"

if (Test-Path $FetchSectorScript) {
    try {
        $SectorResult = & python $FetchSectorScript 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Log "SECTOR: daily ranking fetched successfully"
        } else {
            Write-Log "SECTOR: fetch returned exit code $LASTEXITCODE (non-fatal)"
            # Log output for debugging
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

# ─── TiMi Market Data ──────────────────────────────────────────────────────
# Fetch real A-share K-line data + compute technical indicators for TiMi.
# Uses '--all' mode to cover watchlist + alpha top 50.
Write-Log "TIMI-DATA: fetching market data for TiMi engine..."

$TiMiDataScript = Join-Path $ProjectRoot "scripts\timi_market_data.py"

if (Test-Path $TiMiDataScript) {
    try {
        $TiMiDataResult = & python $TiMiDataScript --all 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Log "TIMI-DATA: market data fetched successfully"
        } else {
            Write-Log "TIMI-DATA: fetch returned exit code $LASTEXITCODE (non-fatal)"
        }
        if ($TiMiDataResult) {
            foreach ($line in $TiMiDataResult) {
                Add-Content -Path $LogFile -Value "[timi-data] $line" -Encoding UTF8
            }
        }
    } catch {
        Write-Log "TIMI-DATA: fetch failed (non-fatal): $($_.Exception.Message)"
    }
} else {
    Write-Log "TIMI-DATA: script not found, skipping"
}

# ─── STABLE-TiMi Bridge ─────────────────────────────────────────────────────
# Read STABLE regime + market technical state → compile strategy for TiMi.
# This replaces manual LLM strategy compilation. Output: data/strategies/{today}.json
Write-Log "TIMI-BRIDGE: compiling strategy from STABLE regime + market data..."

$BridgeScript = Join-Path $ProjectRoot "scripts\stable_timi_bridge.py"

if (Test-Path $BridgeScript) {
    try {
        $BridgeResult = & python $BridgeScript --compile --llm 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Log "TIMI-BRIDGE: strategy compiled and deployed"
        } else {
            Write-Log "TIMI-BRIDGE: compile returned exit code $LASTEXITCODE (non-fatal — TiMi will use yesterday's strategy)"
        }
        if ($BridgeResult) {
            foreach ($line in $BridgeResult) {
                Add-Content -Path $LogFile -Value "[timi-bridge] $line" -Encoding UTF8
            }
        }
    } catch {
        Write-Log "TIMI-BRIDGE: compile failed (non-fatal): $($_.Exception.Message)"
    }
} else {
    Write-Log "TIMI-BRIDGE: script not found, skipping"
}

# ─── TiMi Signal Scan ───────────────────────────────────────────────────────
# Run TiMi deterministic signal scan using real market data.
# Output: timi_signals.json (consumed by signal-gen as deterministic fallback).
Write-Log "TIMI-SCAN: running deterministic signal scan..."

$TiMiScanScript = Join-Path $ProjectRoot "scripts\timi_daily.py"

if (Test-Path $TiMiScanScript) {
    try {
        $TiMiScanResult = & python $TiMiScanScript --gate2 green 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Log "TIMI-SCAN: scan completed (TiMi native)"
        } else {
            Write-Log "TIMI-SCAN: scan returned exit code $LASTEXITCODE (LLM fallback — normal)"
        }

        # Archive TiMi signals for overlap verification
        $TiMiArchiveResult = & python scripts/archive_timi_signals.py --update-watchlist 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Log "TIMI-ARCHIVE: signal archived to watchlist"
        }

        # Ingest TiMi signals into signal-store (signals.jsonl) for tracking/backtesting
        try {
            $TiMiIngestResult = & python scripts/ingest_timi_signals.py 2>&1
            if ($LASTEXITCODE -eq 0) {
                Write-Log "TIMI-INGEST: signals ingested to signal-store"
            }
        } catch {
            Write-Log "TIMI-INGEST: ingest failed (non-fatal): $($_.Exception.Message)"
        }
        if ($TiMiScanResult) {
            foreach ($line in $TiMiScanResult) {
                Add-Content -Path $LogFile -Value "[timi-scan] $line" -Encoding UTF8
            }
        }
    } catch {
        Write-Log "TIMI-SCAN: scan failed (non-fatal): $($_.Exception.Message)"
    }
} else {
    Write-Log "TIMI-SCAN: script not found, skipping"
}

# ─── Build Report Context ────────────────────────────────────────────────
# Regenerate Layer 0/1 summaries from freshly-fetched ETF, sector, and HK data.
# Runs AFTER all data fetches so summaries use the latest data.
Write-Log "CONTEXT: building report context..."

$BuildContextScript = Join-Path $ProjectRoot "scripts\build_report_context.py"

if (Test-Path $BuildContextScript) {
    try {
        $ContextResult = & python $BuildContextScript 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Log "CONTEXT: report context built successfully"
        } else {
            Write-Log "CONTEXT: build returned exit code $LASTEXITCODE (non-fatal)"
        }
    } catch {
        Write-Log "CONTEXT: build failed (non-fatal): $($_.Exception.Message)"
    }
} else {
    Write-Log "CONTEXT: script not found, skipping"
}

# ─── Signal Watchlist Daily Update ──────────────────────────────────────────
# Update tracking prices for active signal watchlist entries
Write-Log "WATCHLIST: updating signal tracking prices..."

$WatchlistScript = Join-Path $ProjectRoot "scripts\signal_watchlist.py"

if (Test-Path $WatchlistScript) {
    try {
        $WatchlistResult = & python $WatchlistScript --update 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Log "WATCHLIST: daily update completed"
        } else {
            Write-Log "WATCHLIST: update returned exit code $LASTEXITCODE (non-fatal)"
        }
        if ($WatchlistResult) {
            foreach ($line in $WatchlistResult) {
                Add-Content -Path $LogFile -Value "[watchlist] $line" -Encoding UTF8
            }
        }
    } catch {
        Write-Log "WATCHLIST: update failed (non-fatal): $($_.Exception.Message)"
    }
} else {
    Write-Log "WATCHLIST: script not found, skipping"
}

# ─── Execute Claude Code headless scan ────────────────────────────────────
Write-Log "START: executing daily scan"

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
<!-- INJECTED BY run-daily-scan.ps1: authoritative date information -->
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
    # Session 0 fix: claude -p defaults to C:\WINDOWS\system32, must cd to project
    Set-Location $ProjectRoot
    # Fix: ensure UTF-8 encoding when piping to native commands (Session 0 defaults to ASCII)
    $OutputEncoding = [Text.Encoding]::UTF8
    [Console]::OutputEncoding = [Text.Encoding]::UTF8
    # Pipe prompt via stdin to avoid shell interpretation of special chars (>, --, etc.)
    $Output = $FullPrompt | claude -p 2>&1
    $ExitCode = $LASTEXITCODE
}
catch {
    Write-Log "ERROR: Claude Code execution exception: $($_.Exception.Message)"
    $ExitCode = -1
}

$Duration = [math]::Round(((Get-Date) - $StartTime).TotalMinutes, 1)
Write-Log "END: scan finished (exit=$ExitCode, duration=$Duration min)"

# ─── Save output ──────────────────────────────────────────────────────────
if ($Output) {
    foreach ($line in $Output) {
        Add-Content -Path $LogFile -Value $line -Encoding UTF8
    }
}
Write-Log "=== log saved: $LogFile ==="

# ─── Verify report ────────────────────────────────────────────────────────
$ReportFile = Join-Path $ProjectRoot "reports\daily\$DateStr-watchlist-scan.md"
if (Test-Path $ReportFile) {
    Write-Log "OK: report generated: $ReportFile"
    $ReportContent = Get-Content $ReportFile -Raw
    if ($ReportContent -match "SCAN_COMPLETE") {
        Write-Log "OK: SCAN_COMPLETE marker confirmed"
    }
    else {
        Write-Log "WARN: report exists but missing SCAN_COMPLETE marker"
    }
}
else {
    Write-Log "WARN: report file not found: $ReportFile"
}

Write-Log "=== my-bot daily scan end ==="
exit $ExitCode