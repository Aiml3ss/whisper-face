@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" %*
set "WHISPER_FACE_EXIT=%ERRORLEVEL%"
echo.
if not "%WHISPER_FACE_EXIT%"=="0" echo Installation failed with exit code %WHISPER_FACE_EXIT%.
if "%~1"=="" pause
exit /b %WHISPER_FACE_EXIT%
