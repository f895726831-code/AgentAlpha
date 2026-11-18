<#
.SYNOPSIS
  Start WSL2 factor-lab API server
.DESCRIPTION
  Triggered by Windows Task Scheduler at system startup and 08:00 on trading days.
  factor-lab 已配置为 WSL2 systemd 服务，本脚本仅验证其健康状态。
  如 WSL2 未运行，systemd 会在 VM 启动时自动拉起 factor-lab。
  Logs: logs/cron/startup-factor-lab.txt
#>

$ErrorActionPreference = "Continue"
$ProjectRoot = "d:\AgentAlpha\my-bot"
$LogDir = Join-Path $ProjectRoot "logs\cron"
$StartupLog = Join-Path $LogDir "startup-factor-lab.txt"

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
"STARTUP $(Get-Date -Format 'o')" | Out-File -FilePath $StartupLog -Encoding utf8 -Append

function Write-Log {
    param([string]$Message)
    $Timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "$Timestamp $Message" | Out-File -FilePath $StartupLog -Encoding utf8 -Append
    Write-Host "$Timestamp $Message"
}

Write-Log "=== factor-lab startup ==="

# ─── Ensure WSL2 is running (systemd 会在 VM 启动时自动拉起 factor-lab) ───
wsl --status 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Log "ERROR: WSL2 not available"
    exit 1
}

# 启动 WSL（如果已停止，此操作会触发 systemd 自动启动 factor-lab）
wsl -d Ubuntu-22.04 -- bash -c "echo 'WSL ready'" 2>&1 | Out-Null
Write-Log "WSL2 VM confirmed running"

# ─── Health check via wsl curl (绕过公司防火墙对 localhost 端口的限制) ───
$maxWait = 30
$waited = 0
while ($waited -lt $maxWait) {
    Start-Sleep -Seconds 3
    $waited += 3
    try {
        # 使用 wsl curl 而非 Invoke-WebRequest — 公司网络阻止 Windows→WSL2 localhost 转发
        $healthJson = wsl -- bash -c "curl -s --connect-timeout 3 http://localhost:8700/api/v1/health" 2>&1
        if ($LASTEXITCODE -eq 0 -and $healthJson -match '"status"\s*:\s*"ok"') {
            $healthObj = $healthJson | ConvertFrom-Json
            Write-Log "OK: factor-lab healthy (qlib=$($healthObj.qlib_ok), rd_agent=$($healthObj.rd_agent_available), deepseek=$($healthObj.deepseek_ok), factors=$($healthObj.factors_count))"
            exit 0
        }
    } catch {
        Write-Log "Waiting... ($waited s)"
    }
}

Write-Log "WARN: factor-lab did not respond within ${maxWait}s"
Write-Log "Check: wsl systemctl status factor-lab"
exit 1