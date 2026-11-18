# Check task actual config
$t = Get-ScheduledTask -TaskName "MyBot-DailyScan" -ErrorAction SilentlyContinue
if (-not $t) {
    Write-Host "TASK NOT FOUND"
    exit 1
}
Write-Host "=== Task Actions ==="
$t.Actions | Format-List Execute, Arguments
Write-Host "=== Task Settings ==="
$t.Settings | Format-List ExecutionTimeLimit, DisallowDemandStartOnBattery, RunOnlyIfIdle, MultipleInstances, AllowStartIfOnBatteries, DontStopIfGoingOnBatteries
Write-Host "=== Task Principal ==="
$t.Principal | Format-List UserId, RunLevel, LogonType