<#
.SYNOPSIS
  Run quant-signal-factory daily pipeline + sync to FM_trade
.DESCRIPTION
  Triggered by Windows Task Scheduler at 08:05 on trading days.
  Steps (dependency chain enforced):
    0:   akshare health check
    0.5: WSL2 + factor-lab readiness
    1.0: Macro Asset Allocation (Layer 0, STABLE/HMM)
    1.1: Data Update (akshare → Qlib bin conversion)  ← CRITICAL: downstream depends on this
    1.2: Sector Data Collection (31 SW industries)
    1.3: Sector Signal Generation (Layer 1 ML predictions)
    2:   CSI 500 ML Ensemble (Alpha360: gbdt_360+xgboost_360+catboost_360)
    2b:  CSI 300 ML Ensemble (Alpha158 fallback)
    2.5: CSI 500 Infer-Only (Alpha158, Alpha360特征末日期不完整)
    2.5b: CSI 300 Infer-Only
    2.55: P1 Factor Incremental Update (v5.0)
    2.6: P1 Factor Consensus (v5.0 FactorFactory Bridge)
    3:   Sync to FM_trade
    3.5: VWAP Factor Merge
    4:   Signal Watchlist Init
    5:   Build Report Context (Layer 0/1)
    5.1: TiMi Market Data Build
    5.5: HK Stock Data
    5.6: ETF Daily Performance
    5.7: Sector Ranking
    6:   ML+P1 Signal Tracker
    6.5: v4.3 vs v5.0 Signal Comparison
  Logs: logs/cron/signal-factory-pipeline.log
#>

$ErrorActionPreference = "Continue"
$ProjectRoot = "d:\AgentAlpha\my-bot"
$LogDir = Join-Path $ProjectRoot "logs\cron"
$PipelineLog = Join-Path $LogDir "signal-factory-pipeline.log"

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
"STARTUP $(Get-Date -Format 'o')" | Out-File -FilePath $PipelineLog -Encoding utf8 -Append

function Write-Log {
    param([string]$Message)
    $Timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "$Timestamp $Message" | Out-File -FilePath $PipelineLog -Encoding utf8 -Append
    Write-Host "$Timestamp $Message"
}

Write-Log "=== signal-factory pipeline start ==="
$PipelineStart = Get-Date

# ─── Trading day check ───
$DayOfWeek = (Get-Date).DayOfWeek
if (($DayOfWeek -eq [System.DayOfWeek]::Saturday) -or ($DayOfWeek -eq [System.DayOfWeek]::Sunday)) {
    Write-Log "SKIP: weekend"
    exit 0
}

$TradingCalendar = Join-Path $ProjectRoot "src\data\trading-calendar-2026.json"
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

# ─── Shared variables ───
$today = Get-Date -Format "yyyy-MM-dd"
$env:PYTHONIOENCODING = "utf-8"

# ─── Step 0: akshare Health Check ───
Write-Log "Step 0: akshare Health Check"
$StartTime = Get-Date
$HealthScript = "D:/AgentAlpha/signal-factory/scripts/akshare_health.py"
$HealthOutputFile = Join-Path $ProjectRoot "data\output\akshare_health.json"
try {
    python $HealthScript --quick --json --output $HealthOutputFile 2>&1 | Out-Null
    # Parse classification from JSON file (more reliable than stdout capture)
    if (Test-Path $HealthOutputFile) {
        $HealthData = Get-Content $HealthOutputFile -Raw -Encoding UTF8 | ConvertFrom-Json
        $HealthClass = $HealthData.classification
        $HealthVer = $HealthData.version
        $HealthDuration = [math]::Round(((Get-Date) - $StartTime).TotalSeconds, 1)
        Write-Log "  Health: $HealthClass ($($HealthVer.installed), latest=$($HealthVer.latest), $HealthDuration s)"
        if ($HealthClass -eq "BLOCKED") {
            Write-Log "  FATAL: akshare Tier 1 blocked. Pipeline Step 1 will fail. Aborting."
            Write-Log "  Check: $HealthOutputFile for details"
            Write-Log "=== pipeline ABORTED (akshare BLOCKED) ==="
            exit 1
        } elseif ($HealthClass -eq "DEGRADED") {
            Write-Log "  WARN: akshare DEGRADED — pipeline will use fallback data sources"
        }
    } else {
        $HealthDuration = [math]::Round(((Get-Date) - $StartTime).TotalSeconds, 1)
        Write-Log "  WARN: health check output not found ($HealthDuration s), proceeding"
    }
} catch {
    $HealthDuration = [math]::Round(((Get-Date) - $StartTime).TotalSeconds, 1)
    Write-Log "  WARN: health check failed ($HealthDuration s): $_ — proceeding"
}

# ─── Step 0.5: WSL2 + factor-lab Readiness Check ───
Write-Log "Step 0.5: WSL2 + factor-lab Readiness"
$StartTime = Get-Date
try {
    wsl --status 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Log "  ERROR: WSL2 not available, skipping factor-lab check"
    } else {
        # Ensure WSL VM is running (systemd auto-starts factor-lab on VM boot)
        wsl -d Ubuntu-22.04 -- bash -c "echo 'ready'" 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            # Health check via wsl curl (bypasses corporate firewall localhost block)
            $healthJson = wsl -- bash -c "curl -s --connect-timeout 5 http://localhost:8700/api/v1/health" 2>&1
            if ($LASTEXITCODE -eq 0 -and $healthJson -match '"status"\s*:\s*"ok"') {
                $healthObj = $healthJson | ConvertFrom-Json
                $wslDuration = [math]::Round(((Get-Date) - $StartTime).TotalSeconds, 1)
                Write-Log "  OK: factor-lab healthy (qlib=$($healthObj.qlib_ok), factors=$($healthObj.factors_count), $wslDuration s)"
            } else {
                # Factor-lab not responding — try restart via start-factor-lab
                Write-Log "  WARN: factor-lab not responding, attempting restart..."
                $startResult = & "d:\AgentAlpha\my-bot\scripts\start-factor-lab.ps1" 2>&1
                $wslDuration = [math]::Round(((Get-Date) - $StartTime).TotalSeconds, 1)
                if ($LASTEXITCODE -eq 0) {
                    Write-Log "  OK: factor-lab restarted ($wslDuration s)"
                } else {
                    Write-Log "  WARN: factor-lab still unreachable — paper enrichment will be skipped ($wslDuration s)"
                }
            }
        } else {
            Write-Log "  WARN: WSL2 VM not reachable — paper enrichment will be skipped"
        }
    }
} catch {
    $wslDuration = [math]::Round(((Get-Date) - $StartTime).TotalSeconds, 1)
    Write-Log "  WARN: WSL2 check failed ($wslDuration s): $_"
}

# ─── Step 1.0: Macro Asset Allocation (Layer 0) ───
Write-Log "Step 1.0: Macro Asset Allocation (Layer 0)"
$StartTime = Get-Date
Set-Location D:/AgentAlpha/signal-factory

$macroResult = python scripts/run_macro_allocation.py --stable --regime hmm 2>&1
$macroExit = $LASTEXITCODE
$macroDuration = [math]::Round(((Get-Date) - $StartTime).TotalSeconds, 1)

if ($macroExit -eq 0) {
    Write-Log "  OK: STABLE macro allocation ($macroDuration s)"
} else {
    Write-Log "  WARN: STABLE macro allocation exit=$macroExit ($macroDuration s)"
    Write-Log "  $($macroResult -join '; ')"
}

# ─── Step 1: Data Update ───
Write-Log "Step 1: Data Update"
$StartTime = Get-Date
Set-Location D:/AgentAlpha/signal-factory

$dataResult = python scripts/update_daily.py --end $today 2>&1
$dataExit = $LASTEXITCODE
$dataDuration = [math]::Round(((Get-Date) - $StartTime).TotalSeconds, 1)

if ($dataExit -eq 0) {
    Write-Log "  OK: data update ($dataDuration s)"
    $script:DataOk = $true
} else {
    Write-Log "  FATAL: data update exit=$dataExit ($dataDuration s)"
    Write-Log "  $($dataResult -join '; ')"
    Write-Log "  Downstream signal generation (Step 2-2.6) will be SKIPPED"
    $script:DataOk = $false
}

# ─── Step 1.2: Sector Data Collection (Layer 1, 31 SW industries) ───
Write-Log "Step 1.2: Sector Data Collection (31 SW industries)"
$StartTime = Get-Date
Set-Location D:/AgentAlpha/signal-factory

$sectorDataResult = python scripts/collect_sector_data.py 2>&1
$sectorDataExit = $LASTEXITCODE
$sectorDataDuration = [math]::Round(((Get-Date) - $StartTime).TotalSeconds, 1)

if ($sectorDataExit -eq 0) {
    Write-Log "  OK: sector data collection ($sectorDataDuration s)"
} else {
    Write-Log "  WARN: sector data collection exit=$sectorDataExit ($sectorDataDuration s)"
    Write-Log "  $($sectorDataResult -join '; ')"
}

# ─── Step 1.3: Sector Signal Generation (Layer 1 ML predictions) ───
Write-Log "Step 1.3: Sector Signal Generation (Layer 1 ML)"
$StartTime = Get-Date
Set-Location D:/AgentAlpha/signal-factory

$sectorSignalResult = python scripts/generate_sector_signals.py 2>&1
$sectorSignalExit = $LASTEXITCODE
$sectorSignalDuration = [math]::Round(((Get-Date) - $StartTime).TotalSeconds, 1)

if ($sectorSignalExit -eq 0) {
    Write-Log "  OK: sector signal generation ($sectorSignalDuration s)"
} else {
    Write-Log "  WARN: sector signal generation exit=$sectorSignalExit ($sectorSignalDuration s)"
    Write-Log "  $($sectorSignalResult -join '; ')"
}

# ─── Step 2: ML Ensemble Signal Generation (CSI 500) ───
if ($script:DataOk -eq $false) {
    Write-Log "Step 2: CSI 500 ML Ensemble — SKIPPED (data update failed)"
    Write-Log "Step 2b: CSI 300 ML Ensemble — SKIPPED (data update failed)"
    Write-Log "Step 2.5: CSI 500 Infer-Only — SKIPPED (data update failed)"
    Write-Log "Step 2.5b: CSI 300 Infer-Only — SKIPPED (data update failed)"
    Write-Log "Step 2.55: P1 Factor Update — SKIPPED (data update failed)"
    Write-Log "Step 2.6: P1 Factor Consensus — SKIPPED (data update failed)"
    $script:SignalsOk = $false
    $script:Csi500Failed = $true
} else {
Write-Log "Step 2: CSI 500 ML Ensemble"
$StartTime = Get-Date
Set-Location D:/AgentAlpha/signal-factory

$stdOutput = Join-Path "data/output" "alpha_signals.json"
$signalResult = python -m src.factors.alpha_signals --ensemble --models gbdt_360 xgboost_360 catboost_360 --instruments csi500 --date $today --no-train --top-n 50 --neutralize-industry --output $stdOutput 2>&1
$signalExit = $LASTEXITCODE
$signalDuration = [math]::Round(((Get-Date) - $StartTime).TotalSeconds, 1)

if ($signalExit -eq 0) {
    Write-Log "  OK: CSI 500 signal generation ($signalDuration s)"
    $oosLine = ($signalResult | Select-String "\[OOS 权重\]" | Select-Object -First 1).Line
    if ($oosLine) { Write-Log "  $oosLine" }
} else {
    Write-Log "  ERROR: CSI 500 signal generation failed exit=$signalExit ($signalDuration s)"
    Write-Log "  $($signalResult -join '; ')"
    Write-Log "  DEGRADED: will attempt CSI 300 + infer-only with fallback"
    $script:Csi500Failed = $true
}

# ─── Step 2b: ML Ensemble Signal Generation (CSI 300) ───
Write-Log "Step 2b: CSI 300 ML Ensemble"
$StartTime = Get-Date
Set-Location D:/AgentAlpha/signal-factory

$csi300Output = Join-Path "data/output" "alpha_signals_csi300.json"
$csi300Result = python -m src.factors.alpha_signals --ensemble --models catboost xgboost gbdt --instruments csi300 --date $today --no-train --top-n 50 --oos-weight --neutralize-industry --output $csi300Output 2>&1
$csi300Exit = $LASTEXITCODE
$csi300Duration = [math]::Round(((Get-Date) - $StartTime).TotalSeconds, 1)

if ($csi300Exit -eq 0) {
    Write-Log "  OK: CSI 300 signal generation ($csi300Duration s)"
    $oosLine = ($csi300Result | Select-String "\[OOS 权重\]" | Select-Object -First 1).Line
    if ($oosLine) { Write-Log "  $oosLine" }
} else {
    Write-Log "  ERROR: CSI 300 signal generation failed exit=$csi300Exit ($csi300Duration s)"
    Write-Log "  $($csi300Result -join '; ')"
    exit 1
}

# ─── Step 2.5: Infer-Only Signal Generation (CSI 500, skip label constraint) ───
Write-Log "Step 2.5: CSI 500 Infer-Only"
$StartTime = Get-Date
Set-Location D:/AgentAlpha/signal-factory

$inferOutput = Join-Path "data/output" "alpha_signals_latest.json"
$inferResult = python -m src.factors.alpha_signals --ensemble --models catboost xgboost gbdt --instruments csi500 --date $today --no-train --infer-only --top-n 50 --oos-weight --neutralize-industry --output $inferOutput 2>&1
$inferExit = $LASTEXITCODE
$inferDuration = [math]::Round(((Get-Date) - $StartTime).TotalSeconds, 1)

if ($inferExit -eq 0) {
    Write-Log "  OK: CSI 500 infer-only ($inferDuration s)"
    $oosLine = ($inferResult | Select-String "\[OOS 权重\]" | Select-Object -First 1).Line
    if ($oosLine) { Write-Log "  $oosLine" }
} else {
    Write-Log "  WARN: CSI 500 infer-only failed exit=$inferExit ($inferDuration s)"
    Write-Log "  $($inferResult -join '; ')"
}

# ─── Step 2.5b: Infer-Only Signal Generation (CSI 300, skip label constraint) ───
Write-Log "Step 2.5b: CSI 300 Infer-Only"
$StartTime = Get-Date
Set-Location D:/AgentAlpha/signal-factory

$inferCsi300Output = Join-Path "data/output" "alpha_signals_latest_csi300.json"
$inferCsi300Result = python -m src.factors.alpha_signals --ensemble --models catboost xgboost gbdt --instruments csi300 --date $today --no-train --infer-only --top-n 50 --oos-weight --neutralize-industry --output $inferCsi300Output 2>&1
$inferCsi300Exit = $LASTEXITCODE
$inferCsi300Duration = [math]::Round(((Get-Date) - $StartTime).TotalSeconds, 1)

if ($inferCsi300Exit -eq 0) {
    Write-Log "  OK: CSI 300 infer-only ($inferCsi300Duration s)"
    $oosLine = ($inferCsi300Result | Select-String "\[OOS 权重\]" | Select-Object -First 1).Line
    if ($oosLine) { Write-Log "  $oosLine" }
} else {
    Write-Log "  WARN: CSI 300 infer-only failed exit=$inferCsi300Exit ($inferCsi300Duration s)"
    Write-Log "  $($inferCsi300Result -join '; ')"
}

# ─── Step 2.55: Incremental P1 Factor Update (v5.0) ───
Write-Log "Step 2.55: Incremental P1 Factor Update (v5.0)"
$StartTime = Get-Date
Set-Location D:/AgentAlpha/my-bot/scripts/wsl2

$p1IncrResult = python scripts/increment_p1_factors.py --lookback 60 2>&1
$p1IncrExit = $LASTEXITCODE
$p1IncrDuration = [math]::Round(((Get-Date) - $StartTime).TotalSeconds, 1)

if ($p1IncrExit -eq 0) {
    Write-Log "  OK: P1 factor update ($p1IncrDuration s)"
    $p1PatchedLine = ($p1IncrResult | Select-String "patched" | Select-Object -Last 1).Line
    if ($p1PatchedLine) { Write-Log "  $p1PatchedLine" }
} else {
    Write-Log "  WARN: P1 factor update exit=$p1IncrExit ($p1IncrDuration s)"
    Write-Log "  $($p1IncrResult -join '; ')"
}

# ─── Step 2.6: Phase 1 Factor Consensus (v5.0 FactorFactory Bridge) ───
Write-Log "Step 2.6: Phase 1 Factor Consensus (v5.0)"
$StartTime = Get-Date
Set-Location D:/AgentAlpha/my-bot/scripts/wsl2

$p1Result = python scripts/generate_p1_signals.py --date $today 2>&1
$p1Exit = $LASTEXITCODE
$p1Duration = [math]::Round(((Get-Date) - $StartTime).TotalSeconds, 1)

if ($p1Exit -eq 0) {
    Write-Log "  OK: Phase 1 consensus ($p1Duration s)"
    $p1FactorsLine = ($p1Result | Select-String "\[Bridge\]" | Select-Object -Last 1).Line
    if ($p1FactorsLine) { Write-Log "  $p1FactorsLine" }
} else {
    Write-Log "  WARN: Phase 1 consensus exit=$p1Exit ($p1Duration s)"
    Write-Log "  $($p1Result -join '; ')"
}

    $script:SignalsOk = (-not $script:Csi500Failed)  # true unless CSI 500 failed
}  # end if DataOk (signal generation guard)

# ─── Step 3: Sync to FM_trade ───
if ($script:DataOk -eq $false) {
    Write-Log "Step 3: Sync to FM_trade — SKIPPED (data update failed)"
} else {
    Write-Log "Step 3: Sync to FM_trade"
    $StartTime = Get-Date
    Set-Location D:/AgentAlpha/my-bot

    $syncResult = python scripts/sync_signal_factory.py --paper 2>&1
    $syncExit = $LASTEXITCODE
    $syncDuration = [math]::Round(((Get-Date) - $StartTime).TotalSeconds, 1)

    if ($syncExit -eq 0) {
        Write-Log "  OK: sync + paper enrichment ($syncDuration s)"
    } else {
        Write-Log "  WARN: sync exit=$syncExit ($syncDuration s)"
        Write-Log "  $($syncResult -join '; ')"
    }
}

# ─── Step 3.5: VWAP Factor Merge ───
Write-Log "Step 3.5: VWAP Factor Merge (IC=0.356, Phase 1 verified)"
$StartTime = Get-Date
Set-Location D:/AgentAlpha/my-bot

$alphaLatest = Join-Path $ProjectRoot "data\output\alpha_signals_latest.json"
if (Test-Path $alphaLatest) {
    $vwapResult = python scripts/generate_vwap_signals.py --date $today --merge --output $alphaLatest 2>&1
    $vwapExit = $LASTEXITCODE
    $vwapDuration = [math]::Round(((Get-Date) - $StartTime).TotalSeconds, 1)
    if ($vwapExit -eq 0) {
        Write-Log "  OK: VWAP merge ($vwapDuration s)"
    } else {
        Write-Log "  WARN: VWAP merge exit=$vwapExit ($vwapDuration s)"
    }
} else {
    Write-Log "  SKIP: alpha_signals_latest.json not found for VWAP merge"
}

# ─── Step 4: Signal Watchlist Init ───
Write-Log "Step 4: Signal Watchlist Init"
$StartTime = Get-Date
Set-Location D:/AgentAlpha/my-bot

$watchlistResult = python scripts/signal_watchlist.py --init 2>&1
$watchlistExit = $LASTEXITCODE
$watchlistDuration = [math]::Round(((Get-Date) - $StartTime).TotalSeconds, 1)

if ($watchlistExit -eq 0) {
    Write-Log "  OK: signal watchlist init ($watchlistDuration s)"
} else {
    Write-Log "  WARN: signal watchlist init exit=$watchlistExit ($watchlistDuration s)"
    Write-Log "  $($watchlistResult -join '; ')"
}

# ─── Step 5: Build Report Context (Layer 0/1 summaries) ───
Write-Log "Step 5: Build Report Context (Layer 0/1)"
$StartTime = Get-Date
Set-Location D:/AgentAlpha/my-bot

$contextResult = python scripts/build_report_context.py 2>&1
$contextExit = $LASTEXITCODE
$contextDuration = [math]::Round(((Get-Date) - $StartTime).TotalSeconds, 1)

if ($contextExit -eq 0) {
    Write-Log "  OK: report context built ($contextDuration s)"
} else {
    Write-Log "  WARN: report context failed exit=$contextExit ($contextDuration s)"
    Write-Log "  $($contextResult -join '; ')"
}

# ─── Step 5.1: TiMi Market Data Build (real OHLCV + MA/RSI from akshare) ───
Write-Log "Step 5.1: TiMi Market Data Build"
$StartTime = Get-Date
Set-Location D:/AgentAlpha/my-bot

$timiMdResult = python scripts/build_timi_market_data.py --max-stocks 50 2>&1
$timiMdExit = $LASTEXITCODE
$timiMdDuration = [math]::Round(((Get-Date) - $StartTime).TotalSeconds, 1)

if ($timiMdExit -eq 0) {
    Write-Log "  OK: TiMi market data ($timiMdDuration s)"
} else {
    Write-Log "  WARN: TiMi market data exit=$timiMdExit ($timiMdDuration s)"
}

# ─── Step 5.5: HK Stock Data (akshare stock_hk_spot_em, not blocked by EastMoney) ───
Write-Log "Step 5.5: HK Stock Data (from portfolio watchlist)"
$StartTime = Get-Date
Set-Location D:/AgentAlpha/my-bot

$hkResult = python scripts/hk_stock_data.py 2>&1
$hkExit = $LASTEXITCODE
$hkDuration = [math]::Round(((Get-Date) - $StartTime).TotalSeconds, 1)

if ($hkExit -eq 0) {
    Write-Log "  OK: HK stock data ($hkDuration s)"
} else {
    Write-Log "  WARN: HK stock data failed exit=$hkExit ($hkDuration s)"
    Write-Log "  $($hkResult -join '; ')"
}

# ─── Step 5.6: ETF Daily Performance (akshare THS, replaces WebSearch) ───
Write-Log "Step 5.6: ETF Daily Performance (11-class assets)"
$StartTime = Get-Date
Set-Location D:/AgentAlpha/my-bot

$etfResult = python scripts/fetch_etf_daily.py 2>&1
$etfExit = $LASTEXITCODE
$etfDuration = [math]::Round(((Get-Date) - $StartTime).TotalSeconds, 1)

if ($etfExit -eq 0) {
    Write-Log "  OK: ETF daily data ($etfDuration s)"
} else {
    Write-Log "  WARN: ETF daily data failed exit=$etfExit ($etfDuration s)"
    Write-Log "  $($etfResult -join '; ')"
}

# ─── Step 5.7: Sector Ranking (akshare THS, replaces WebSearch) ───
Write-Log "Step 5.7: Sector Ranking (31 SW industries)"
$StartTime = Get-Date
Set-Location D:/AgentAlpha/my-bot

$sectorResult = python scripts/fetch_sector_ranking.py 2>&1
$sectorExit = $LASTEXITCODE
$sectorDuration = [math]::Round(((Get-Date) - $StartTime).TotalSeconds, 1)

if ($sectorExit -eq 0) {
    Write-Log "  OK: sector ranking ($sectorDuration s)"
} else {
    Write-Log "  WARN: sector ranking failed exit=$sectorExit ($sectorDuration s)"
    Write-Log "  $($sectorResult -join '; ')"
}

# ─── Step 6: ML Signal Tracker (track Top 20 alpha signals over 5d) ───
Write-Log "Step 6: ML+P1 Signal Tracker"
$StartTime = Get-Date
Set-Location D:/AgentAlpha/my-bot

$trackerResult = python scripts/signal_tracker.py 2>&1
$trackerExit = $LASTEXITCODE
$trackerDuration = [math]::Round(((Get-Date) - $StartTime).TotalSeconds, 1)

if ($trackerExit -eq 0) {
    Write-Log "  OK: signal tracker ($trackerDuration s)"
} else {
    Write-Log "  WARN: signal tracker failed exit=$trackerExit ($trackerDuration s)"
    Write-Log "  $($trackerResult -join '; ')"
}

# ─── Step 6.5: v4.3 vs v5.0 Signal Comparison (P1 incremental contribution) ───
Write-Log "Step 6.5: v4.3 vs v5.0 Signal Comparison"
$StartTime = Get-Date
Set-Location D:/AgentAlpha/my-bot

$comparisonResult = python scripts/signal_comparison.py --json 2>&1
$comparisonExit = $LASTEXITCODE
$comparisonDuration = [math]::Round(((Get-Date) - $StartTime).TotalSeconds, 1)

if ($comparisonExit -eq 0) {
    Write-Log "  OK: signal comparison ($comparisonDuration s)"
    $overlapLine = ($comparisonResult | Select-String "Overlap" | Select-Object -First 1).Line
    if ($overlapLine) { Write-Log "  $overlapLine" }
} else {
    Write-Log "  WARN: signal comparison exit=$comparisonExit ($comparisonDuration s)"
}

# ─── Summary ───
$totalDuration = [math]::Round(((Get-Date) - $PipelineStart).TotalSeconds, 1)
Write-Log "=== signal-factory pipeline complete (total: ${totalDuration}s, CSI 500 + CSI 300) ==="
exit 0