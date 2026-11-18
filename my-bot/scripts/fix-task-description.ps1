# Fix-MyBotTaskDescription.ps1
# 修复计划任务描述中的乱码（UTF-8 → GBK 编码问题）

$taskName = "MyBot-DailyScan"
$newDescription = "my-bot daily watchlist scan - triggered at 16:05 on trading days"

# 用 Set-ScheduledTask 更新描述
$task = Get-ScheduledTask -TaskName $taskName
$task | Set-ScheduledTask -Description $newDescription

Write-Host "Description updated to: $newDescription"

# 验证
$updated = Get-ScheduledTask -TaskName $taskName
Write-Host "Current description: $($updated.Description)"