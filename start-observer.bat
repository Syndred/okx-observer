@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\dashboard_gui.ps1"
if errorlevel 1 (
  echo Failed to start the observer. Install Docker Desktop, then try again.
  pause
)
