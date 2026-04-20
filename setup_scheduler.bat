@echo off
title Earnings Bot — Task Scheduler Setup
cd /d "%~dp0"

set PYTHON=C:\Users\krish\AppData\Local\Programs\Python\Python313\python.exe
set DIR=C:\Users\krish\Documents\Claude Stock Trader

echo ============================================
echo  Setting up Windows Task Scheduler
echo ============================================
echo.
echo This will create 6 scheduled tasks:
echo   - Scanner    : 11:00 PM daily
echo   - Validator  : 8:00 AM weekdays
echo   - Executor   : 9:35 AM weekdays
echo   - Monitor    : Every hour 10AM-4PM weekdays
echo   - Reporter   : 4:30 PM weekdays
echo   - Learner    : 9:00 PM Sundays
echo.
pause

:: ── Task 1: Scanner (11 PM daily) ──────────────────────────────
schtasks /delete /tn "EarningsBot_Scanner" /f >nul 2>&1
schtasks /create ^
  /tn "EarningsBot_Scanner" ^
  /tr "\"%PYTHON%\" \"%DIR%\src\scanner.py\"" ^
  /sc DAILY ^
  /st 23:00 ^
  /sd 04/19/2026 ^
  /rl HIGHEST ^
  /f
echo [1/6] Scanner scheduled at 11:00 PM daily

:: ── Task 2: Validator (8 AM weekdays) ──────────────────────────
schtasks /delete /tn "EarningsBot_Validator" /f >nul 2>&1
schtasks /create ^
  /tn "EarningsBot_Validator" ^
  /tr "\"%PYTHON%\" \"%DIR%\src\validator.py\"" ^
  /sc WEEKLY ^
  /d MON,TUE,WED,THU,FRI ^
  /st 08:00 ^
  /rl HIGHEST ^
  /f
echo [2/6] Validator scheduled at 8:00 AM weekdays

:: ── Task 3: Executor (9:35 AM weekdays) ────────────────────────
schtasks /delete /tn "EarningsBot_Executor" /f >nul 2>&1
schtasks /create ^
  /tn "EarningsBot_Executor" ^
  /tr "\"%PYTHON%\" \"%DIR%\src\executor.py\"" ^
  /sc WEEKLY ^
  /d MON,TUE,WED,THU,FRI ^
  /st 09:35 ^
  /rl HIGHEST ^
  /f
echo [3/6] Executor scheduled at 9:35 AM weekdays

:: ── Task 4: Monitor (hourly 10 AM - 4 PM weekdays) ─────────────
schtasks /delete /tn "EarningsBot_Monitor" /f >nul 2>&1
schtasks /create ^
  /tn "EarningsBot_Monitor" ^
  /tr "\"%PYTHON%\" \"%DIR%\src\monitor.py\"" ^
  /sc WEEKLY ^
  /d MON,TUE,WED,THU,FRI ^
  /st 10:00 ^
  /ri 60 ^
  /du 0006:00 ^
  /rl HIGHEST ^
  /f
echo [4/6] Monitor scheduled hourly 10AM-4PM weekdays

:: ── Task 5: Reporter (4:30 PM weekdays) ────────────────────────
schtasks /delete /tn "EarningsBot_Reporter" /f >nul 2>&1
schtasks /create ^
  /tn "EarningsBot_Reporter" ^
  /tr "\"%PYTHON%\" \"%DIR%\src\reporter.py\"" ^
  /sc WEEKLY ^
  /d MON,TUE,WED,THU,FRI ^
  /st 16:30 ^
  /rl HIGHEST ^
  /f
echo [5/6] Reporter scheduled at 4:30 PM weekdays

:: ── Task 6: Learner (9 PM Sundays) ─────────────────────────────
schtasks /delete /tn "EarningsBot_Learner" /f >nul 2>&1
schtasks /create ^
  /tn "EarningsBot_Learner" ^
  /tr "\"%PYTHON%\" \"%DIR%\src\learner.py\"" ^
  /sc WEEKLY ^
  /d SUN ^
  /st 21:00 ^
  /rl HIGHEST ^
  /f
echo [6/6] Learner scheduled at 9:00 PM Sundays

:: ── Task 7: Dashboard auto-update (every 30 min weekdays) ──────
schtasks /delete /tn "EarningsBot_Dashboard" /f >nul 2>&1
schtasks /create ^
  /tn "EarningsBot_Dashboard" ^
  /tr "\"%PYTHON%\" \"%DIR%\update_dashboard.py\"" ^
  /sc WEEKLY ^
  /d MON,TUE,WED,THU,FRI ^
  /st 09:00 ^
  /ri 30 ^
  /du 0008:00 ^
  /rl HIGHEST ^
  /f
echo [7/7] Dashboard auto-update every 30 min (9AM-5PM weekdays)

echo.
echo ============================================
echo  All tasks created successfully.
echo  To view them: open Task Scheduler and look
echo  under Task Scheduler Library for EarningsBot_*
echo ============================================
echo.
pause
