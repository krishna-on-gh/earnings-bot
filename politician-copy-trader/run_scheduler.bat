@echo off
REM Starts the continuous scheduler — keeps running, copies trades every 30 min
REM Close this window to stop.
title Politician Copy Trader — Scheduler
cd /d "%~dp0"
"C:\Users\krish\AppData\Local\Programs\Thonny\python.exe" main.py --run
pause
