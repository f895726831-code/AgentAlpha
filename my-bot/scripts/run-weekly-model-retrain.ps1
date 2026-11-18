<#
.SYNOPSIS
  Weekly model retraining for CSI 500 + CSI 300
.DESCRIPTION
  Triggered by Windows Task Scheduler on Monday 07:30 (before daily pipeline).
  Retrains CatBoost, XGBoost, GBDT models on both CSI 500 and CSI 300.
  Prediction output goes to temp files to avoid polluting alpha_signals.json —
  the 16:00 daily pipeline will regenerate fresh signals from today's data.
  Logs: logs/cron/weekly-model-retrain.log
#>

$ErrorActionPreference = "Continue"
$ProjectRoot = "d:\AgentAlpha\my-bot"
$LogDir = Join-Path $ProjectRoot "logs\cron"
$RetrainLog = Join-Path $LogDir "weekly-model-retrain.log"

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
"" | Out-File -FilePath $RetrainLog -Encoding utf8 -Append
"$(Get-Date -Format 'o') === weekly model retrain start ===" | Out-File -FilePath $RetrainLog -Encoding utf8 -Append

function Write-Log {
    param([string]$Message)
    $Timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "$Timestamp $Message" | Out-File -FilePath $RetrainLog -Encoding utf8 -Append
    Write-Host "$Timestamp $Message"
}

$DayOfWeek = (Get-Date).DayOfWeek
$IsMonday = ($DayOfWeek -eq [System.DayOfWeek]::Monday)
$Today = Get-Date -Format "yyyy-MM-dd"

if (-not $IsMonday) {
    Write-Log "SKIP: not Monday (today: $DayOfWeek)"
    exit 0
}

# ─── Trading day check ───
$TradingCalendar = Join-Path $ProjectRoot "src\data\trading-calendar-2026.json"
if (Test-Path $TradingCalendar) {
    try {
        $Calendar = Get-Content $TradingCalendar -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($Calendar.closed_dates -contains $Today) {
            Write-Log "SKIP: holiday ($Today)"
            exit 0
        }
    } catch {
        Write-Log "WARN: calendar read failed, assuming trading day"
    }
}

Write-Log "=== Monday: Model retraining (train-only, temp output) ==="
$PipelineStart = Get-Date

# ─── Retrain CSI 500 ───
# 写入临时文件 _retrain_tmp_csi500.json，避免污染 alpha_signals.json
# 16:00 日频管线会基于当日数据重新生成标准信号
Write-Log "Step 1: Retrain CSI 500 models (catboost, xgboost, gbdt)"
$StartTime = Get-Date
Set-Location D:/AgentAlpha/signal-factory
$env:PYTHONIOENCODING = "utf-8"

$csi500Tmp = Join-Path "data/output" "_retrain_tmp_csi500.json"
$csi500Result = python -m src.factors.alpha_signals --ensemble --models catboost xgboost gbdt --instruments csi500 --date $Today --force-retrain --top-n 50 --output $csi500Tmp 2>&1
$csi500Exit = $LASTEXITCODE
$csi500Duration = [math]::Round(((Get-Date) - $StartTime).TotalSeconds, 1)

# 清理临时文件
Remove-Item -Path "D:/AgentAlpha/signal-factory/$csi500Tmp" -Force -ErrorAction SilentlyContinue

if ($csi500Exit -eq 0) {
    Write-Log "  OK: CSI 500 retrain ($csi500Duration s)"
} else {
    Write-Log "  ERROR: CSI 500 retrain failed exit=$csi500Exit ($csi500Duration s)"
    Write-Log "  $($csi500Result -join '; ')"
}

# ─── Retrain CSI 300 ───
# 同样写入临时文件，16:00 日频管线会重新生成
Write-Log "Step 2: Retrain CSI 300 models (catboost, xgboost, gbdt)"
$StartTime = Get-Date
Set-Location D:/AgentAlpha/signal-factory
$env:PYTHONIOENCODING = "utf-8"

$csi300Tmp = Join-Path "data/output" "_retrain_tmp_csi300.json"
$csi300Result = python -m src.factors.alpha_signals --ensemble --models catboost xgboost gbdt --instruments csi300 --date $Today --force-retrain --top-n 50 --neutralize-industry --output $csi300Tmp 2>&1
$csi300Exit = $LASTEXITCODE
$csi300Duration = [math]::Round(((Get-Date) - $StartTime).TotalSeconds, 1)

# 清理临时文件
Remove-Item -Path "D:/AgentAlpha/signal-factory/$csi300Tmp" -Force -ErrorAction SilentlyContinue

if ($csi300Exit -eq 0) {
    Write-Log "  OK: CSI 300 retrain ($csi300Duration s)"
} else {
    Write-Log "  ERROR: CSI 300 retrain failed exit=$csi300Exit ($csi300Duration s)"
    Write-Log "  $($csi300Result -join '; ')"
}

# ─── Summary ───
$totalDuration = [math]::Round(((Get-Date) - $PipelineStart).TotalSeconds, 1)
Write-Log "=== weekly model retrain complete (total: ${totalDuration}s, CSI 500 + CSI 300) ==="

$exitCode = 0
if ($csi500Exit -ne 0 -and $csi300Exit -ne 0) {
    Write-Log "FATAL: both CSI 500 and CSI 300 retrain failed"
    $exitCode = 1
}
exit $exitCode