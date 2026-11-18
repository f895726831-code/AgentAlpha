<#
.SYNOPSIS
  Run factor-lab weekly tasks (paper mining + factor mining + compute + combine)
.DESCRIPTION
  Triggered by Windows Task Scheduler on Saturday 10:00 and Monday 08:00.
  Saturday: paper factor mining + RD-Agent factor mining (research)
  Monday:   factor computation + combination + export
  Logs: logs/cron/weekly-factor-lab.log
#>

$ErrorActionPreference = "Continue"
$ProjectRoot = "d:\AgentAlpha\my-bot"
$LogDir = Join-Path $ProjectRoot "logs\cron"
$WeeklyLog = Join-Path $LogDir "weekly-factor-lab.log"

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
"" | Out-File -FilePath $WeeklyLog -Encoding utf8 -Append
"$(Get-Date -Format 'o') === factor-lab weekly start ===" | Out-File -FilePath $WeeklyLog -Encoding utf8 -Append

function Write-Log {
    param([string]$Message)
    $Timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "$Timestamp $Message" | Out-File -FilePath $WeeklyLog -Encoding utf8 -Append
    Write-Host "$Timestamp $Message"
}

$DayOfWeek = (Get-Date).DayOfWeek
$IsSaturday = ($DayOfWeek -eq [System.DayOfWeek]::Saturday)
$IsMonday = ($DayOfWeek -eq [System.DayOfWeek]::Monday)
$ApiBase = "http://localhost:8700"
$Today = Get-Date -Format "yyyy-MM-dd"

# ─── WSL2 readiness + factor-lab health check ───
Write-Log "Step 0: WSL2 + factor-lab readiness"
$WslOk = $false
try {
    wsl --status 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        # Ensure WSL VM is running (systemd auto-starts factor-lab on VM boot)
        wsl -d Ubuntu-22.04 -- bash -c "echo 'ready'" 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-Log "  WSL2 VM: running"
            # Health check via wsl curl (bypasses corporate firewall localhost block)
            $healthJson = wsl -- bash -c "curl -s --connect-timeout 5 http://localhost:8700/api/v1/health" 2>&1
            if ($LASTEXITCODE -eq 0 -and $healthJson -match '"status"\s*:\s*"ok"') {
                $healthObj = $healthJson | ConvertFrom-Json
                Write-Log "  factor-lab: OK (qlib=$($healthObj.qlib_ok), rd_agent=$($healthObj.rd_agent_available), factors=$($healthObj.factors_count))"
                $WslOk = $true
            } else {
                Write-Log "  factor-lab: not responding, attempting restart..."
            }
        } else {
            Write-Log "  WSL2 VM: not reachable, attempting start..."
        }
    } else {
        Write-Log "  WSL2: not available"
    }
} catch {
    Write-Log "  WSL2 check error: $_"
}

# ─── Restart factor-lab if needed ───
if (-not $WslOk) {
    Write-Log "  Running start-factor-lab.ps1..."
    $startResult = & "d:\AgentAlpha\my-bot\scripts\start-factor-lab.ps1" 2>&1
    Write-Log "  start result: $($startResult -join '; ')"
    Start-Sleep -Seconds 10
    # Re-check
    try {
        $healthJson = wsl -- bash -c "curl -s --connect-timeout 5 http://localhost:8700/api/v1/health" 2>&1
        if ($LASTEXITCODE -eq 0 -and $healthJson -match '"status"\s*:\s*"ok"') {
            $healthObj = $healthJson | ConvertFrom-Json
            Write-Log "  factor-lab: OK after restart (qlib=$($healthObj.qlib_ok))"
        } else {
            Write-Log "  FATAL: factor-lab still not reachable after restart, aborting"
            exit 1
        }
    } catch {
        Write-Log "  FATAL: factor-lab health re-check failed, aborting"
        exit 1
    }
}

# ═══════════════════════════════════════════════════════════════
#  Saturday: Research tasks
# ═══════════════════════════════════════════════════════════════
if ($IsSaturday) {
    Write-Log "=== Saturday: Research tasks ==="

    # ─── Paper mining ───
    Write-Log "Step 1: Paper factor mining (DeepSeek)"
    try {
        $paperBody = @{
            mode = "extract"
            max_papers = 5
            date = $Today
        } | ConvertTo-Json
        $paperResult = Invoke-RestMethod -Uri "$ApiBase/api/v1/papers/mine" -Method POST -Body $paperBody -ContentType "application/json" -TimeoutSec 30
        Write-Log "  Task ID: $($paperResult.task_id), status: $($paperResult.status)"

        # Poll for completion (max 20 min)
        $maxWait = 1200
        $waited = 0
        while ($waited -lt $maxWait) {
            Start-Sleep -Seconds 30
            $waited += 30
            try {
                $taskStatus = Invoke-RestMethod -Uri "$ApiBase/api/v1/tasks/$($paperResult.task_id)" -TimeoutSec 10
                if ($taskStatus.status -eq "completed") {
                    Write-Log "  Paper mining completed: $($taskStatus.result | ConvertTo-Json -Depth 2 -Compress)"
                    break
                } elseif ($taskStatus.status -eq "failed") {
                    Write-Log "  Paper mining failed: $($taskStatus.error)"
                    break
                }
                Write-Log "  Paper mining: $($taskStatus.status) ($($waited)s)"
            } catch {
                Write-Log "  Paper mining poll error: $_"
                break
            }
        }
        if ($waited -ge $maxWait) {
            Write-Log "  Paper mining: timeout after ${maxWait}s"
        }
    } catch {
        Write-Log "  Paper mining error: $_"
    }

    # ─── RD-Agent factor mining ───
    Write-Log "Step 2: RD-Agent factor mining"
    try {
        $mineBody = @{
            max_rounds = 5
            date = $Today
            config = "config/rd_agent.yaml"
        } | ConvertTo-Json
        $mineResult = Invoke-RestMethod -Uri "$ApiBase/api/v1/factors/mine" -Method POST -Body $mineBody -ContentType "application/json" -TimeoutSec 30
        Write-Log "  Task ID: $($mineResult.task_id), status: $($mineResult.status)"

        # Poll for completion (max 30 min)
        $maxWait = 1800
        $waited = 0
        while ($waited -lt $maxWait) {
            Start-Sleep -Seconds 60
            $waited += 60
            try {
                $taskStatus = Invoke-RestMethod -Uri "$ApiBase/api/v1/tasks/$($mineResult.task_id)" -TimeoutSec 10
                if ($taskStatus.status -eq "completed") {
                    Write-Log "  Factor mining completed: $($taskStatus.result | ConvertTo-Json -Depth 2 -Compress)"
                    break
                } elseif ($taskStatus.status -eq "failed") {
                    Write-Log "  Factor mining failed: $($taskStatus.error)"
                    break
                }
                Write-Log "  Factor mining: $($taskStatus.status) ($($waited)s)"
            } catch {
                Write-Log "  Factor mining poll error: $_"
                break
            }
        }
        if ($waited -ge $maxWait) {
            Write-Log "  Factor mining: timeout after ${maxWait}s"
        }
    } catch {
        Write-Log "  Factor mining error: $_"
    }
}

# ═══════════════════════════════════════════════════════════════
#  Monday: Factor computation + combination + export
# ═══════════════════════════════════════════════════════════════
if ($IsMonday) {
    Write-Log "=== Monday: Factor computation + combination ==="

    # ─── Factor compute ───
    Write-Log "Step 1: Compute all factors"
    try {
        $computeBody = @{
            date = $Today
            instruments = "all"
        } | ConvertTo-Json
        $computeResult = Invoke-RestMethod -Uri "$ApiBase/api/v1/factors/compute" -Method POST -Body $computeBody -ContentType "application/json" -TimeoutSec 120
        Write-Log "  Computed $($computeResult.factor_count) factors"
        Write-Log "  Signals: $($computeResult.signals | ConvertTo-Json -Depth 2 -Compress)"
    } catch {
        Write-Log "  Factor compute error: $_"
    }

    # ─── Factor combine ───
    Write-Log "Step 2: Combine factors"
    try {
        $combineBody = @{
            method = "ic_weighted"
            top_k = 50
            date = $Today
        } | ConvertTo-Json
        $combineResult = Invoke-RestMethod -Uri "$ApiBase/api/v1/factors/combine" -Method POST -Body $combineBody -ContentType "application/json" -TimeoutSec 120
        Write-Log "  Combined $($combineResult.factor_count) factors → $($combineResult.rankings.Count) rankings"
        if ($combineResult.rankings.Count -gt 0) {
            Write-Log "  Top 5: $($combineResult.rankings[0..4] | ConvertTo-Json -Depth 2 -Compress)"
        }
    } catch {
        Write-Log "  Factor combine error: $_"
    }

    # ─── Export to FM_trade ───
    Write-Log "Step 3: Export to FM_trade"
    try {
        $exportPath = Join-Path $ProjectRoot "data\output\factor_lab_weekly.json"
        $combineResult | ConvertTo-Json -Depth 4 | Out-File -FilePath $exportPath -Encoding utf8
        Write-Log "  Exported to $exportPath"
    } catch {
        Write-Log "  Export error: $_"
    }
}

# ─── Summary ───
Write-Log "=== factor-lab weekly complete ==="

# Only keep on Saturday/Monday
if (-not ($IsSaturday -or $IsMonday)) {
    Write-Log "SKIP: not Saturday or Monday (today: $DayOfWeek)"
    exit 0
}