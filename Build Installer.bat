@echo off
rem ---------------------------------------------------------------------
rem  Rebuilds AVG Studio from the CURRENT source and produces:
rem      "AVG APP\"                 the app, ready to run
rem      "installer\AVG-Setup.exe"  the installer for another machine
rem  Run this whenever the app changes. Takes a few minutes.
rem ---------------------------------------------------------------------
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\make_release.ps1" %*
echo.
pause
