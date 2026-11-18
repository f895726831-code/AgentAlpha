<#
Register-SignalFactoryTask.ps1 — 注册信号工厂盘前自动化计划任务
以管理员身份运行此脚本
#>

$taskName  = "SignalFactory-DailyPipeline"
$scriptPath = "D:\AgentAlpha\signal-factory\scripts\run-signal-factory.ps1"

# 检查是否已存在
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "任务已存在，先删除旧任务..."
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$scriptPath`""

$trigger = New-ScheduledTaskTrigger -Daily -At "08:00"

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
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description "信号工厂: 每日盘前自动化流水线 — 数据更新 → Alpha信号生成 → 同步到FM_trade (08:00)"

Write-Host "=== 任务注册完成 ==="
Write-Host "任务名称: $taskName"
Write-Host "触发时间: 每天 08:00"
Write-Host "脚本路径: $scriptPath"

# 验证
$registered = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($registered) {
    Write-Host "状态: $($registered.State)"
    Write-Host "下次运行: $($registered.NextRunTime)"
} else {
    Write-Host "ERROR: 任务注册失败"
    exit 1
}