# Register-MyBotTask.ps1 — 注册 my-bot 盘后全量扫描计划任务
# 以管理员身份运行此脚本

$taskName  = "MyBot-DailyScan"
$scriptPath = "d:\AgentAlpha\my-bot\scripts\run-daily-scan.ps1"

# 检查是否已存在
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "任务已存在，先删除旧任务..."
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$scriptPath`""

$trigger = New-ScheduledTaskTrigger -Daily -At "16:05"

$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERNAME" `
    -RunLevel Highest

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -DisallowDemandStartOnBattery:$false `
    -RunOnlyIfIdle:$false `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2)

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description "my-bot: daily after-close watchlist scan (16:05 trading days)"

Write-Host "=== 任务注册完成 ==="
Write-Host "任务名称: $taskName"
Write-Host "触发时间: 每天 16:05"
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