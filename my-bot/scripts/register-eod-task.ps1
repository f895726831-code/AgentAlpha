# Register-MyBotEODTask.ps1
# Register MyBot EOD data refresh scheduled task.
# Run as Administrator.
#
# Background: 2026-10-26 morning brief CSI300 data error caused by
# missing Friday EOD data refresh. This task ensures daily 15:30 refresh.
#
# Usage (Admin PowerShell):
#   .\scripts\register-eod-task.ps1
#   .\scripts\register-eod-task.ps1 -UnregisterOnly

param(
    [switch]$UnregisterOnly = $false
)

$taskName   = "MyBot-EODDataRefresh"
$scriptPath = "d:\AgentAlpha\my-bot\scripts\run-eod-data.ps1"

# ── Check for elevation ──
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "ERROR: Administrator privileges required." -ForegroundColor Red
    Write-Host "  Right-click PowerShell -> Run as Administrator"
    Write-Host "  Then: .\scripts\register-eod-task.ps1"
    exit 1
}

# ── Unregister-only mode ──
if ($UnregisterOnly) {
    $existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($existing) {
        Write-Host "Removing task '$taskName'..." -ForegroundColor Yellow
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        Write-Host "Removed." -ForegroundColor Green
    } else {
        Write-Host "Task '$taskName' does not exist."
    }
    exit 0
}

# ── Verify script exists ──
if (-not (Test-Path $scriptPath)) {
    Write-Host "ERROR: Script not found: $scriptPath" -ForegroundColor Red
    exit 1
}

# ── Remove existing task if present ──
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Task already exists, removing old version..." -ForegroundColor Yellow
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}

# ── Build task components ──

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$scriptPath`""

# Daily at 15:30 (after market close, before 16:00 signal factory)
$trigger = New-ScheduledTaskTrigger -Daily -At "15:30"

# Matches register-task.ps1 pattern. Use update-tasks.ps1 for "run whether logged on" mode.
$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERNAME" `
    -RunLevel Highest

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -RunOnlyIfIdle:$false `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 2)

# ── Register ──
try {
    Register-ScheduledTask `
        -TaskName $taskName `
        -Action $action `
        -Trigger $trigger `
        -Principal $principal `
        -Settings $settings `
        -Description "my-bot: EOD data refresh - ETF daily + CSI300 K-line JSON (15:30, v4.3.1)"
} catch {
    Write-Host "ERROR: Registration failed: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host ""
    Write-Host "Manual fallback (schtasks):"
    Write-Host '  schtasks /Create /TN MyBot-EODDataRefresh /TR "powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File d:\AgentAlpha\my-bot\scripts\run-eod-data.ps1" /SC DAILY /ST 15:30 /F'
    exit 1
}

# ── Verify ──
$registered = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($registered) {
    Write-Host ""
    Write-Host "=== Task registered ===" -ForegroundColor Green
    Write-Host ""
    Write-Host "  Task Name : $taskName"
    Write-Host "  Schedule  : Daily at 15:30"
    Write-Host "  Script    : $scriptPath"
    Write-Host "  User      : $env:USERDOMAIN\$env:USERNAME"
    Write-Host "  State     : $($registered.State)"
    Write-Host "  Next Run  : $($registered.NextRunTime)"
    Write-Host ""
    Write-Host "Manual test:"
    Write-Host "  powershell -ExecutionPolicy Bypass -File `"$scriptPath`""
    Write-Host ""
    Write-Host "Manual trigger:"
    Write-Host "  Start-ScheduledTask -TaskName '$taskName'"
    Write-Host ""
    Write-Host "View logs:"
    Write-Host "  ls d:\AgentAlpha\my-bot\logs\cron\*-eod.log | select -Last 5"
    Write-Host ""
    Write-Host "To remove:"
    Write-Host "  .\scripts\register-eod-task.ps1 -UnregisterOnly"
    Write-Host ""
} else {
    Write-Host "ERROR: Post-registration verification failed" -ForegroundColor Red
    exit 1
}