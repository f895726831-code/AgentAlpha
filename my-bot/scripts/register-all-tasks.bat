@echo off
REM Register all my-bot scheduled tasks
REM Right-click → Run as Administrator
REM Or run from elevated PowerShell: .\register-all-tasks.bat

echo === Registering my-bot scheduled tasks ===
echo.

echo [1/9] Weekly Model Retrain (Monday 07:30)
schtasks /delete /tn "MyBot-ModelRetrain-Weekly" /f 2>nul
schtasks /create /tn "MyBot-ModelRetrain-Weekly" ^
  /tr "powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File \"d:\AgentAlpha\my-bot\scripts\run-weekly-model-retrain.ps1\"" ^
  /sc weekly /d MON /st 07:30 ^
  /f
echo.

echo [2/9] Factor-Lab Startup (system startup)
schtasks /delete /tn "MyBot-FactorLab" /f 2>nul
schtasks /create /tn "MyBot-FactorLab" ^
  /tr "powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File \"d:\AgentAlpha\my-bot\scripts\start-factor-lab.ps1\"" ^
  /sc onstart ^
  /delay 0001:00 ^
  /f
echo.

echo [3/9] Factor-Lab Wakeup (07:55 trading days)
schtasks /delete /tn "MyBot-FactorLab-Wakeup" /f 2>nul
schtasks /create /tn "MyBot-FactorLab-Wakeup" ^
  /tr "powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File \"d:\AgentAlpha\my-bot\scripts\start-factor-lab.ps1\"" ^
  /sc daily /st 07:55 ^
  /f
echo.

echo [4/9] Signal Factory Pipeline (16:00 trading days)
schtasks /delete /tn "MyBot-SignalFactory" /f 2>nul
schtasks /create /tn "MyBot-SignalFactory" ^
  /tr "powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File \"d:\AgentAlpha\my-bot\scripts\run-signal-factory-pipeline.ps1\"" ^
  /sc daily /st 16:00 ^
  /f
echo.

echo [5/9] Morning Brief (08:35 trading days)
schtasks /delete /tn "MyBot-MorningBrief" /f 2>nul
schtasks /create /tn "MyBot-MorningBrief" ^
  /tr "powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File \"d:\AgentAlpha\my-bot\scripts\run-morning-brief.ps1\"" ^
  /sc daily /st 08:35 ^
  /f
echo.

echo [6/9] Midday Review (11:30 trading days)
schtasks /delete /tn "MyBot-MiddayReview" /f 2>nul
schtasks /create /tn "MyBot-MiddayReview" ^
  /tr "powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File \"d:\AgentAlpha\my-bot\scripts\run-midday-review.ps1\"" ^
  /sc daily /st 11:30 ^
  /f
echo.

echo [7/9] Daily Scan (17:30 trading days, after SignalFactory completes)
schtasks /delete /tn "MyBot-DailyScan" /f 2>nul
schtasks /create /tn "MyBot-DailyScan" ^
  /tr "powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File \"d:\AgentAlpha\my-bot\scripts\run-daily-scan.ps1\"" ^
  /sc daily /st 17:30 ^
  /f
echo.

echo [8/9] Factor-Lab Weekly Research (Saturday 10:00)
schtasks /delete /tn "MyBot-FactorLab-Weekly-Research" /f 2>nul
schtasks /create /tn "MyBot-FactorLab-Weekly-Research" ^
  /tr "powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File \"d:\AgentAlpha\my-bot\scripts\run-weekly-factor-lab.ps1\"" ^
  /sc weekly /d SAT /st 10:00 ^
  /f
echo.

echo [9/9] Factor-Lab Weekly Compute (Monday 08:00)
schtasks /delete /tn "MyBot-FactorLab-Weekly-Compute" /f 2>nul
schtasks /create /tn "MyBot-FactorLab-Weekly-Compute" ^
  /tr "powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File \"d:\AgentAlpha\my-bot\scripts\run-weekly-factor-lab.ps1\"" ^
  /sc weekly /d MON /st 08:00 ^
  /f
echo.

echo [10/10] Health Check (17:45 trading days)
schtasks /delete /tn "MyBot-HealthCheck" /f 2>nul
schtasks /create /tn "MyBot-HealthCheck" ^
  /tr "powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File \"d:\AgentAlpha\my-bot\scripts\run-healthcheck.ps1\"" ^
  /sc daily /st 17:45 ^
  /f
echo.

echo === Verification ===
schtasks /query /tn "MyBot-ModelRetrain-Weekly" /v /fo list 2>nul | findstr /i "TaskName Status"
schtasks /query /tn "MyBot-FactorLab" /v /fo list 2>nul | findstr /i "TaskName Status"
schtasks /query /tn "MyBot-FactorLab-Wakeup" /v /fo list 2>nul | findstr /i "TaskName Status"
schtasks /query /tn "MyBot-SignalFactory" /v /fo list 2>nul | findstr /i "TaskName Status"
schtasks /query /tn "MyBot-MorningBrief" /v /fo list 2>nul | findstr /i "TaskName Status"
schtasks /query /tn "MyBot-MiddayReview" /v /fo list 2>nul | findstr /i "TaskName Status"
schtasks /query /tn "MyBot-DailyScan" /v /fo list 2>nul | findstr /i "TaskName Status"
schtasks /query /tn "MyBot-FactorLab-Weekly-Research" /v /fo list 2>nul | findstr /i "TaskName Status"
schtasks /query /tn "MyBot-FactorLab-Weekly-Compute" /v /fo list 2>nul | findstr /i "TaskName Status"
schtasks /query /tn "MyBot-HealthCheck" /v /fo list 2>nul | findstr /i "TaskName Status"

echo.
echo === Done ===
echo Tasks registered:
echo   [Daily]
echo   MyBot-FactorLab               - system startup (1 min delay)
echo   MyBot-FactorLab-Wakeup        - 07:55 daily
echo   MyBot-SignalFactory           - 16:00 daily
echo   MyBot-MorningBrief            - 08:35 daily
echo   MyBot-MiddayReview            - 11:30 daily
echo   MyBot-DailyScan               - 17:30 daily (after SignalFactory)
echo   MyBot-HealthCheck             - 17:45 daily (after all tasks)
echo   [Weekly]
echo   MyBot-ModelRetrain-Weekly     - Monday 07:30 (CSI 500 + CSI 300 retrain)
echo   MyBot-FactorLab-Weekly-Research - Saturday 10:00 (paper + RD-agent mining)
echo   MyBot-FactorLab-Weekly-Compute  - Monday 08:00 (compute + combine + export)
echo.
echo Daily pipeline timing:
echo   07:30  [Mon] model retrain (CSI 500 + CSI 300)
echo   07:55  factor-lab starts (WSL2 API server)
echo   08:00  [Mon] weekly factor compute + combine
echo   08:35  morning brief (reads alpha_signals.json from previous close)
echo   11:30  midday review
echo   16:00  data update + ML ensemble (CSI 500 + CSI 300) + sync + paper enrichment
echo   17:30  daily scan (reads latest alpha_signals from today's pipeline)
echo   17:45  health check (heartbeat + e2e, alerts to alerts/daily/)
echo.
echo Weekly pipeline timing:
echo   Mon 07:30  model retrain (CSI 500 + CSI 300, CatBoost/XGBoost/GBDT)
echo   Sat 10:00  paper factor mining (DeepSeek) + RD-Agent factor mining
echo   Mon 08:00  factor computation + combination + export to FM_trade
echo.
echo IMPORTANT: Open Task Scheduler (taskschd.msc), right-click each task,
echo select Properties, check "Run whether user is logged on or not"
echo to prevent Ctrl+C termination on session change.

pause