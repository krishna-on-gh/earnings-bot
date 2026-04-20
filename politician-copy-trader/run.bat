@echo off
REM Politician Copy Trader — launcher
REM Usage:
REM   run.bat          — single pass (score + copy new trades)
REM   run.bat --run    — start continuous scheduler (runs 24/7)
REM   run.bat --status — show portfolio & trade summary
REM   run.bat --score  — refresh politician rankings only

cd /d "%~dp0"
"C:\Users\krish\AppData\Local\Programs\Thonny\python.exe" main.py %*
pause
