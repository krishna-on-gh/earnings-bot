@echo off
title Earnings Bot — Run Task
cd /d "%~dp0"
set PYTHON=C:\Users\krish\AppData\Local\Programs\Python\Python313\python.exe

echo ============================================
echo  Earnings Trading Bot — Task Runner
echo ============================================
echo.
echo  1. Scanner    (find earnings plays - takes 5-10 min)
echo  2. Validator  (pre-market checks)
echo  3. Executor   (place trades - REAL ORDERS)
echo  4. Monitor    (check positions)
echo  5. Reporter   (daily P&L report)
echo  6. Learner    (weekly strategy update)
echo  7. Update dashboard (refresh dashboard.html)
echo.
set /p CHOICE="Enter number (1-7): "

if "%CHOICE%"=="1" (
    echo Running Scanner...
    "%PYTHON%" src\scanner.py
)
if "%CHOICE%"=="2" (
    echo Running Validator...
    "%PYTHON%" src\validator.py
)
if "%CHOICE%"=="3" (
    echo Running Executor - this will place REAL paper trades...
    pause
    "%PYTHON%" src\executor.py
)
if "%CHOICE%"=="4" (
    echo Running Monitor...
    "%PYTHON%" src\monitor.py
)
if "%CHOICE%"=="5" (
    echo Running Reporter...
    "%PYTHON%" src\reporter.py
)
if "%CHOICE%"=="6" (
    echo Running Learner...
    "%PYTHON%" src\learner.py
)
if "%CHOICE%"=="7" (
    echo Updating dashboard...
    call update_dashboard.bat
)

echo.
echo Done.
pause
