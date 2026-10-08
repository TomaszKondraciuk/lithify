@echo off
rem Lithify for Windows: double-click this file.
rem
rem It runs install.ps1 from this folder (or downloads it when this file is on its own) in Windows
rem PowerShell: that gets the computer ready, asking before it installs anything, then opens the
rem Lithify wizard in the web browser, which finds the speaker and installs Lithify on it.
rem
rem Windows may warn first ("Windows protected your PC", or "The publisher could not be
rem verified"): choose "More info" and "Run anyway", or "Run".
rem (No labels and no GOTO in here: this file may arrive with Unix line endings.)
setlocal
title Lithify
echo(
echo   ==============================================================
echo     Lithify - Spotify Connect for your Lithe Audio speaker
echo     Lithify - Spotify Connect dla glosnikow Lithe Audio
echo   ==============================================================
echo(
echo   This gets your computer ready (it asks before it installs
echo   anything), then opens a page in your web browser that finds
echo   the speaker and installs Lithify on it. The first time takes
echo   15-30 minutes; the computer and the speaker must be on the
echo   same network.
echo(
set "LITHIFY_LAUNCHER=1"
set "LITHIFY_LAUNCHER_FILE=%~f0"
set "PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%PS%" set "PS=powershell.exe"
set "URL=https://raw.githubusercontent.com/OWNER/lithify/main/install.ps1"
if defined LITHIFY_INSTALL_URL set "URL=%LITHIFY_INSTALL_URL%"
if exist "%~dp0install.ps1" "%PS%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
if not exist "%~dp0install.ps1" "%PS%" -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor 3072; try { $s = Invoke-RestMethod -UseBasicParsing '%URL%' -ErrorAction Stop } catch { Write-Host ('error: could not download the installer: ' + $_.Exception.Message) -ForegroundColor Red; exit 1 }; Invoke-Expression $s; exit $LASTEXITCODE"
set "RC=%ERRORLEVEL%"
echo(
if "%RC%"=="0" echo   Lithify is ready. Enjoy the music!
if "%RC%"=="3010" echo   Windows restarts now. Once you sign in again, Lithify goes on by itself.
if not "%RC%"=="0" if not "%RC%"=="3010" echo   Lithify is not installed yet: the messages above say why, and what to do.
if not "%RC%"=="0" if not "%RC%"=="3010" echo   You can run this again any time: it skips what is already done.
echo(
echo   Press any key to close this window.
pause >nul
exit /b %RC%
