@echo off
title Earnings Bot Dashboard
cd /d "%~dp0"

:: Try Thonny's bundled Python first (confirmed installed)
set PY=C:\Users\krish\AppData\Local\Programs\Python\Python313\python.exe
if exist "%PY%" (
    echo Starting server...
    start "" "http://localhost:8080/dashboard.html"
    "%PY%" -m http.server 8080
    goto end
)

:: Use PowerShell (built into all Windows 10/11 — no install needed)
echo Starting dashboard server via PowerShell...
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0serve.ps1"

if %errorlevel% neq 0 (
    echo.
    echo PowerShell server failed. Trying Python...
    echo.
    for %%P in (
        python
        python3
        py
        "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
        "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
        "%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
        "C:\Python312\python.exe"
        "C:\Python311\python.exe"
        "C:\Python310\python.exe"
    ) do (
        %%P -m http.server 8080 2>nul
        if not errorlevel 1 goto end
    )
    echo.
    echo Could not start server. Please install Python from https://python.org
    echo Then run:  python -m http.server 8080
    echo And open:  http://localhost:8080/dashboard.html
    pause
)
:end
