@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" %*
set "PARROT_EXIT=%ERRORLEVEL%"
echo.
if not "%PARROT_EXIT%"=="0" echo Installation failed with exit code %PARROT_EXIT%.
if "%~1"=="" pause
exit /b %PARROT_EXIT%
