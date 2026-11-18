@echo off
setlocal enabledelayedexpansion
title Register El Nino Tasks

:: Check admin
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] This script requires Administrator privileges.
    echo Right-click this file ^> "Run as administrator"
    echo.
    pause
    exit /b 1
)

echo === MyBot El Nino Pipeline ===
echo Tasks: MyBot-ElNino-Mon + MyBot-ElNino-Thu
echo Schedule: Every Mon + Thu 08:00 (peak period: 2x/week)
echo Script: run-el-nino-pipeline.ps1
echo.

:: --- Monday Task ---
echo [Mon 1/2] Removing old Monday task (if exists)...
schtasks /delete /tn "MyBot-ElNino-Weekly" /f 2>nul
schtasks /delete /tn "MyBot-ElNino-Mon" /f 2>nul

echo [Mon 2/2] Creating Monday task...
schtasks /create ^
  /tn "MyBot-ElNino-Mon" ^
  /tr "powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File \"d:\AgentAlpha\my-bot\scripts\run-el-nino-pipeline.ps1\"" ^
  /sc weekly /d MON /st 08:00 ^
  /f

if %errorlevel% equ 0 (
    echo   [OK] MyBot-ElNino-Mon created
) else (
    echo   [FAIL] MyBot-ElNino-Mon error=%errorlevel%
)

:: --- Thursday Task ---
echo.
echo [Thu 1/2] Removing old Thursday task (if exists)...
schtasks /delete /tn "MyBot-ElNino-Thu" /f 2>nul

echo [Thu 2/2] Creating Thursday task...
schtasks /create ^
  /tn "MyBot-ElNino-Thu" ^
  /tr "powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File \"d:\AgentAlpha\my-bot\scripts\run-el-nino-pipeline.ps1\"" ^
  /sc weekly /d THU /st 08:00 ^
  /f

if %errorlevel% equ 0 (
    echo   [OK] MyBot-ElNino-Thu created
) else (
    echo   [FAIL] MyBot-ElNino-Thu error=%errorlevel%
)

:: --- Summary ---
echo.
echo === Registered Tasks ===
schtasks /query /tn "MyBot-ElNino-Mon" /fo list 2>nul | findstr /i "TaskName Status Schedule"
schtasks /query /tn "MyBot-ElNino-Thu" /fo list 2>nul | findstr /i "TaskName Status Schedule"
echo.
echo To manage: taskschd.msc ^> search "MyBot-ElNino"
echo To run now:  schtasks /run /tn "MyBot-ElNino-Mon"
echo               schtasks /run /tn "MyBot-ElNino-Thu"
echo.
echo Peak period: Nov 2026 (ONI peak 3.2-3.5C). Revert to weekly-only
echo after ENSO peak passes (early 2027) by deleting MyBot-ElNino-Thu.
echo.
pause