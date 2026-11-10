@echo off
setlocal
set "APP_DIR=%~dp0.."
set "VENV_PY=%APP_DIR%\Support Files\.venv\Scripts\python.exe"
if not exist "%VENV_PY%" (
    echo Run "1 Setup.bat" first.
    pause
    exit /b 1
)
"%VENV_PY%" -u "%APP_DIR%\Support Files\transcriber.py" --check
echo.
pause
