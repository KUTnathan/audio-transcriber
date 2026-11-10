@echo off
setlocal
set "APP_DIR=%~dp0"
if "%APP_DIR:~-1%"=="\" set "APP_DIR=%APP_DIR:~0,-1%"
set "SUPPORT_DIR=%APP_DIR%\Support Files"
set "VENV_PY=%SUPPORT_DIR%\.venv\Scripts\python.exe"

if not exist "%VENV_PY%" (
    echo Please finish "1 Setup.bat" first.
    echo.
    pause
    exit /b 1
)

echo ================================================================
echo   Auto Start at Login (Optional - Windows)
echo ================================================================
for /f "delims=" %%S in ('"%VENV_PY%" "%SUPPORT_DIR%\transcriber.py" --manage-autostart status') do set "CUR=%%S"
echo Current status: %CUR%
echo.

if /i "%CUR%"=="OFF" (
    set /p "ANS=Turn ON Auto-Start at Login? [Enter = YES, N = Cancel]: "
    if /i "%ANS%"=="N" goto done
    "%VENV_PY%" "%SUPPORT_DIR%\transcriber.py" --manage-autostart on
) else (
    set /p "ANS=Turn OFF Auto-Start at Login? [Enter = YES, K = Keep ON]: "
    if /i "%ANS%"=="K" goto done
    "%VENV_PY%" "%SUPPORT_DIR%\transcriber.py" --manage-autostart off
)
:done
echo.
pause
