<#
Register-SignalFactoryTasks.ps1 — 注册信号工厂两个计划任务
以管理员身份运行此脚本

  1. SignalFactory-DataUpdate   — 每天 15:30 收盘后数据更新
  2. SignalFactory-Morning      — 每天 08:00 盘前信号生成+同步
#>

$ErrorActionPreference = "Stop"

# ─── 删除旧任务 ────────────────────────────────────────────────────────────
$oldTasks = @("SignalFactory-DailyPipeline", "SignalFactory-DataUpdate", "SignalFactory-Morning")
foreach ($name in $oldTasks) {
    $existing = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    if ($existing) {
        Write-Host "删除旧任务: $name"
        Unregister-ScheduledTask -TaskName $name -Confirm:$false
    }
}

# ─── 任务 1: 收盘后数据更新 (15:30) ────────────────────────────────────────
$task1Name = "SignalFactory-DataUpdate"
$task1Script = "D:\AgentAlpha\signal-factory\scripts\run-data-update.ps1"

$action1 = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$task1Script`""

$trigger1 = New-ScheduledTaskTrigger -Daily -At "15:30"

$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERNAME" `
    -RunLevel Highest

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -RunOnlyIfIdle:$false `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2)

Register-ScheduledTask `
    -TaskName $task1Name `
    -Action $action1 `
    -Trigger $trigger1 `
    -Principal $principal `
    -Settings $settings `
    -Description "信号工厂: 收盘后增量数据更新 (15:30)"

Write-Host "[OK] $task1Name — 每天 15:30"

# ─── 任务 2: 盘前信号生成+同步 (08:00) ────────────────────────────────────
$task2Name = "SignalFactory-Morning"
$task2Script = "D:\AgentAlpha\signal-factory\scripts\run-signal-factory.ps1"

$action2 = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$task2Script`""

$trigger2 = New-ScheduledTaskTrigger -Daily -At "08:00"

Register-ScheduledTask `
    -TaskName $task2Name `
    -Action $action2 `
    -Trigger $trigger2 `
    -Principal $principal `
    -Settings $settings `
    -Description "信号工厂: 盘前 Alpha 信号生成 + 同步到 FM_trade (08:00)"

Write-Host "[OK] $task2Name — 每天 08:00"

# ─── 验证 ──────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "=== 注册完成 ==="
foreach ($name in @($task1Name, $task2Name)) {
    $t = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    if ($t) {
        Write-Host "$name : $($t.State) | 下次: $($t.NextRunTime)"
    } else {
        Write-Host "$name : ERROR - 注册失败"
    }
}