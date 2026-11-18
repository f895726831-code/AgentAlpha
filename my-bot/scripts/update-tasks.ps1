<#
.SYNOPSIS
  Batch update all MyBot scheduled task credentials + logon settings.
.DESCRIPTION
  Run after changing Windows login password.
  Sets password AND "Run whether user is logged on or not" for all 10 MyBot tasks.
  Must be run from an elevated PowerShell terminal.

  Usage:
    .\update-tasks.ps1
    .\update-tasks.ps1 -Password "MyNewPassword"
    .\update-tasks.ps1 -Password "MyNewPassword" -Username "DOMAIN\user"
#>

param(
    [string]$Password,
    [string]$Username
)

$tasks = @(
    "MyBot-ModelRetrain-Weekly",
    "MyBot-FactorLab",
    "MyBot-FactorLab-Wakeup",
    "MyBot-SignalFactory",
    "MyBot-MorningBrief",
    "MyBot-MiddayReview",
    "MyBot-DailyScan",
    "MyBot-FactorLab-Weekly-Research",
    "MyBot-FactorLab-Weekly-Compute",
    "MyBot-HealthCheck"
)

# ── Get username ──
if (-not $Username) {
    $Username = "$env:USERDOMAIN\$env:USERNAME"
    Write-Host "User: $Username" -ForegroundColor Cyan
    $confirm = Read-Host "Is this correct? (Y/n)"
    if ($confirm -eq 'n' -or $confirm -eq 'N') {
        $Username = Read-Host "Enter full username (e.g. DOMAIN\user)"
    }
}

# ── Get password ──
if (-not $Password) {
    $securePwd = Read-Host "Enter Windows login password" -AsSecureString
    $BSTR = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePwd)
    $Password = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto($BSTR)
}

Write-Host ""
Write-Host "Updating $($tasks.Count) tasks..." -ForegroundColor Cyan
Write-Host ""

$failed = @()
foreach ($task in $tasks) {
    try {
        # /RU + /RP enables "Run whether user is logged on or not"
        $result = schtasks.exe /Change /TN $task /RU $Username /RP $Password 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Host "  [OK] $task" -ForegroundColor Green
        } else {
            $errMsg = if ($result -is [array]) { $result -join ' ' } else { "$result" }
            Write-Host "  [FAIL] $task — $errMsg" -ForegroundColor Red
            $failed += $task
        }
    } catch {
        Write-Host "  [FAIL] $task — $($_.Exception.Message)" -ForegroundColor Red
        $failed += $task
    }
}

Write-Host ""
if ($failed.Count -eq 0) {
    Write-Host "All $($tasks.Count) tasks updated — credentials stored, logon type set" -ForegroundColor Green
} else {
    Write-Host "$($failed.Count) tasks failed: $($failed -join ', ')" -ForegroundColor Yellow
    Write-Host "  Run as Administrator and verify task names with:"
    Write-Host "  schtasks /query /fo list | findstr MyBot"
}

Write-Host ""
Write-Host "Verify logon type for each task:"
Write-Host "  schtasks /query /tn MyBot-HealthCheck /v /fo list | findstr /i 'Logon Mode'"
Write-Host "  (Should show 'Logon Mode: Interactive/Background' or 'Password')"
Write-Host ""
Write-Host "Verify health check:"
Write-Host "  d:\AgentAlpha\signal-factory\venv\Scripts\python.exe scripts\healthcheck.py"