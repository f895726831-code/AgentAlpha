@echo off
REM Register my-bot daily scan task with hardened settings
REM Right-click → Run as Administrator

schtasks /delete /tn "MyBot-DailyScan" /f 2>nul

schtasks /create /tn "MyBot-DailyScan" ^
  /tr "powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File \"d:\AgentAlpha\my-bot\scripts\run-daily-scan.ps1\"" ^
  /sc daily /st 16:05 ^
  /ru "%USERNAME%" ^
  /rl HIGHEST ^
  /f

echo.
echo Task registered. Verifying...
schtasks /query /tn "MyBot-DailyScan" /v /fo list | findstr /i "TaskName Status Schedule"

pause