@echo off
rem Lithify for Windows: double-click this file.
rem
rem It runs installer\install.ps1 from this folder (or downloads it when this file is on its own)
rem in Windows PowerShell: that gets the computer ready, asking before it installs anything, then
rem opens the Lithify wizard in the web browser, which finds the speaker and installs Lithify on it.
rem The installer shows its messages in Polish or English, as Windows is shown.
rem
rem Windows may warn first ("The publisher could not be verified", or "Windows protected your
rem PC"): choose "Run", or "More info" and "Run anyway".
rem (No labels and no GOTO in here: this file may arrive with Unix line endings. Plain ASCII only:
rem cmd.exe reads it in the console's code page.)
setlocal
title Lithify
set "LITHIFY_LAUNCHER=1"
set "LITHIFY_LAUNCHER_FILE=%~f0"
set "PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%PS%" set "PS=powershell.exe"
set "URL=https://raw.githubusercontent.com/OWNER/lithify/main/installer/install.ps1"
if defined LITHIFY_INSTALL_URL set "URL=%LITHIFY_INSTALL_URL%"
if exist "%~dp0installer\install.ps1" "%PS%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer\install.ps1"
if not exist "%~dp0installer\install.ps1" "%PS%" -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor 3072; $ProgressPreference = 'SilentlyContinue'; $f = Join-Path ([IO.Path]::GetTempPath()) 'lithify-install.ps1'; try { Invoke-WebRequest -UseBasicParsing -Uri '%URL%' -OutFile $f -ErrorAction Stop } catch { Write-Host ('error: could not download the installer / nie uda' + [char]0x142 + 'o si' + [char]0x119 + ' pobra' + [char]0x107 + ' instalatora: ' + $_.Exception.Message) -ForegroundColor Red; exit 1 }; & '%PS%' -NoProfile -ExecutionPolicy Bypass -File $f; exit $LASTEXITCODE"
set "RC=%ERRORLEVEL%"
rem (the installer said how it went; "pause" asks for a key in the language Windows is shown in)
echo(
pause
exit /b %RC%
