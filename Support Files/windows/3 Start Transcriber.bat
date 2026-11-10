@echo off
setlocal
set "APP_DIR=%~dp0"
if "%APP_DIR:~-1%"=="\" set "APP_DIR=%APP_DIR:~0,-1%"
set "SUPPORT_DIR=%APP_DIR%\Support Files"
set "VENV_PY=%SUPPORT_DIR%\.venv\Scripts\python.exe"

if not exist "%VENV_PY%" (
    echo Setup has not been run yet. Please double-click "1 Setup.bat" first.
    echo.
    pause
    exit /b 1
)

:run_loop
echo ================================================================
echo   Audio Transcriber (Windows)
echo ================================================================
"%VENV_PY%" -u "%SUPPORT_DIR%\transcriber.py"
set "STATUS=%ERRORLEVEL%"

if "%STATUS%"=="2" (
    echo.
    pause
    exit /b 0
)

echo.
echo ================================================================
echo Audio Transcriber is stopped.
set /p "RESTART_CHOICE=Press Enter (or R + Enter) to restart, or type Q + Enter to close: "
if /i "%RESTART_CHOICE%"=="Q" exit /b 0
goto run_loop
