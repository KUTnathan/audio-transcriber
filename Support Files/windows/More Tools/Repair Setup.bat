@echo off
setlocal
set "APP_DIR=%~dp0.."
set "SUPPORT_DIR=%APP_DIR%\Support Files"
set "VENV_PY=%SUPPORT_DIR%\.venv\Scripts\python.exe"

echo === Audio Transcriber — Repair Setup ===
echo.
if exist "%VENV_PY%" (
    "%VENV_PY%" "%SUPPORT_DIR%\transcriber.py" --is-running >nul 2>&1
    if not errorlevel 1 (
        echo Audio Transcriber is currently running in another window.
        echo Please stop it with Control+C before running Repair Setup.
        pause
        exit /b 1
    )
)

set /p "CONFIRM=Type REPAIR and press Enter to rebuild Python setup: "
if /i not "%CONFIRM%"=="REPAIR" (
    echo Repair canceled.
    pause
    exit /b 0
)

if exist "%SUPPORT_DIR%\.venv" rmdir /s /q "%SUPPORT_DIR%\.venv"
if exist "%SUPPORT_DIR%\.python" rmdir /s /q "%SUPPORT_DIR%\.python"
call "%APP_DIR%\1 Setup.bat"
