# Test trading calendar JSON parsing
$path = "d:\AgentAlpha\my-bot\src\data\trading-calendar-2026.json"
try {
    $c = Get-Content $path -Raw -Encoding UTF8 | ConvertFrom-Json
    Write-Host "OK: closed_dates = $($c.closed_dates.Count)"
    Write-Host "OK: makeup_trading_days = $($c.makeup_trading_days.Count)"
    Write-Host "Sample closed: $($c.closed_dates[0])"
    Write-Host "Sample makeup: $($c.makeup_trading_days[0])"
} catch {
    Write-Host "FAIL: $($_.Exception.Message)"
    Write-Host "FAIL line: $($_.InvocationInfo.ScriptLineNumber)"
}