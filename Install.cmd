@echo off
setlocal
cd /d "%~dp0"
rem Size before the run, so a log left by an earlier run is never presented
rem as the record of this one.
set "WHISPER_FACE_LOG_BEFORE=none"
if exist "%~dp0install.log" for %%F in ("%~dp0install.log") do set "WHISPER_FACE_LOG_BEFORE=%%~zF"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" %*
set "WHISPER_FACE_EXIT=%ERRORLEVEL%"
echo.
if "%WHISPER_FACE_EXIT%"=="0" goto :succeeded
echo Installation failed with exit code %WHISPER_FACE_EXIT%.
set "WHISPER_FACE_LOG_AFTER=none"
if exist "%~dp0install.log" for %%F in ("%~dp0install.log") do set "WHISPER_FACE_LOG_AFTER=%%~zF"
if "%WHISPER_FACE_LOG_AFTER%"=="%WHISPER_FACE_LOG_BEFORE%" goto :nolog
echo The whole run, including the error above, was recorded here:
echo   "%~dp0install.log"
echo Attach that file to a bug report; it holds every step that ran.
goto :held
:nolog
echo Setup stopped before it could open a log, so the error above is all
echo there is. Please copy these lines into a bug report.
:held
rem Hold the window open even when arguments were passed. An error that
rem scrolled off the screen is the whole reason this shim exists.
pause
exit /b %WHISPER_FACE_EXIT%

:succeeded
if "%~1"=="" pause
exit /b 0
