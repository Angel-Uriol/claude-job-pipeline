@echo off
rem Double-click me (Windows) to install anything missing and open Job Pipeline.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "launch\start.ps1"
if errorlevel 1 pause
