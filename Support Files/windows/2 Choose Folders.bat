@echo off
setlocal
set "APP_DIR=%~dp0"
if "%APP_DIR:~-1%"=="\" set "APP_DIR=%APP_DIR:~0,-1%"
set "SUPPORT_DIR=%APP_DIR%\Support Files"
set "VENV_PY=%SUPPORT_DIR%\.venv\Scripts\python.exe"
set "STANDALONE_PY=%SUPPORT_DIR%\.python\python.exe"

if exist "%VENV_PY%" (
    "%VENV_PY%" -u "%SUPPORT_DIR%\choose_folders.py"
) else if exist "%STANDALONE_PY%" (
    "%STANDALONE_PY%" -u "%SUPPORT_DIR%\choose_folders.py"
) else (
    echo Please run "1 Setup.bat" once first before choosing folders.
)
echo.
pause
