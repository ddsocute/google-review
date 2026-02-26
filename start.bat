@echo off
setlocal EnableExtensions

rem Simple launcher for Google Maps Restaurant Review AI
rem Double-click this file to start the app.

set "ROOT=%~dp0"

rem Call PowerShell script (all logic is there)
powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%start.ps1"
set "PS_EXIT=%ERRORLEVEL%"

exit /b %PS_EXIT%
