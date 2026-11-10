@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "APP_DIR=%~dp0"
if "%APP_DIR:~-1%"=="\" set "APP_DIR=%APP_DIR:~0,-1%"
set "SUPPORT_DIR=%APP_DIR%\Support Files"
set "TOOLS_DIR=%SUPPORT_DIR%\.tools"
set "UV=%TOOLS_DIR%\uv.exe"
set "PY_DIR=%SUPPORT_DIR%\.python"
set "STANDALONE_PY=%PY_DIR%\python.exe"
set "VENV_DIR=%SUPPORT_DIR%\.venv"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"
set "TOKEN_FILE=%APP_DIR%\My Hugging Face Token.txt"

set "UV_URL=https://github.com/astral-sh/uv/releases/download/0.13.6/uv-x86_64-pc-windows-msvc.zip"
set "PY_URL=https://github.com/astral-sh/python-build-standalone/releases/download/20261026/cpython-3.12.16%%2B20261026-x86_64-pc-windows-msvc-install_only_stripped.tar.gz"

set "UV_CACHE_DIR=%SUPPORT_DIR%\.uv-cache"
set "UV_PYTHON_DOWNLOADS=never"
set "UV_LINK_MODE=copy"

echo ================================================================
echo   Audio Transcriber — One-Time Setup (Windows)
echo ================================================================
echo App folder: %APP_DIR%
echo No administrator password is required.
echo.

if not exist "%TOOLS_DIR%" mkdir "%TOOLS_DIR%"
if not exist "%APP_DIR%\Audio to Transcribe" mkdir "%APP_DIR%\Audio to Transcribe"
if not exist "%APP_DIR%\Finished Transcripts" mkdir "%APP_DIR%\Finished Transcripts"
if not exist "%SUPPORT_DIR%\logs" mkdir "%SUPPORT_DIR%\logs"
if not exist "%SUPPORT_DIR%\models" mkdir "%SUPPORT_DIR%\models"

if not exist "%UV%" (
    echo [Step 1/4] Downloading private package installer (uv)...
    "%SystemRoot%\System32\curl.exe" -fL --retry 3 -o "%TOOLS_DIR%\uv.zip" "%UV_URL%"
    if errorlevel 1 (
        echo ERROR: Could not download uv installer. Check your internet connection.
        pause
        exit /b 1
    )
    "%SystemRoot%\System32\tar.exe" -xf "%TOOLS_DIR%\uv.zip" -C "%TOOLS_DIR%"
    del /q "%TOOLS_DIR%\uv.zip" >nul 2>&1
) else (
    echo [Step 1/4] Private package installer (uv) is already ready.
)

if not exist "%STANDALONE_PY%" (
    echo.
    echo [Step 2/4] Downloading private Python 3.12...
    "%SystemRoot%\System32\curl.exe" -fL --retry 3 -o "%SUPPORT_DIR%\python.tar.gz" "%PY_URL%"
    if errorlevel 1 (
        echo ERROR: Could not download standalone Python 3.12.
        pause
        exit /b 1
    )
    if exist "%PY_DIR%" rmdir /s /q "%PY_DIR%"
    "%SystemRoot%\System32\tar.exe" -xzf "%SUPPORT_DIR%\python.tar.gz" -C "%SUPPORT_DIR%"
    move "%SUPPORT_DIR%\python" "%PY_DIR%" >nul
    del /q "%SUPPORT_DIR%\python.tar.gz" >nul 2>&1
) else (
    echo [Step 2/4] Private Python 3.12 is already installed.
)

if not exist "%VENV_PY%" (
    echo.
    echo [Step 3/4] Creating private Python environment...
    "%UV%" venv --allow-existing --python "%STANDALONE_PY%" "%VENV_DIR%"
)

echo.
echo [Step 3/4] Installing transcription packages (may take 2-5 minutes the first time)...
"%UV%" pip install --python "%VENV_PY%" --only-binary :all: --find-links "%SUPPORT_DIR%\wheels" -r "%SUPPORT_DIR%\requirements.lock"
if errorlevel 1 (
    echo ERROR: Package installation failed.
    pause
    exit /b 1
)

if not exist "%TOKEN_FILE%" (
    >"%TOKEN_FILE%" echo YOUR_TOKEN_HERE
)

echo.
echo [Step 4/4] Checking Hugging Face token and AI models...
"%VENV_PY%" -u "%SUPPORT_DIR%\transcriber.py" --download-models
if errorlevel 1 (
    echo.
    echo ================================================================
    echo Almost done! If you still need to add your Hugging Face token:
    echo   1. Create a "Read" token at https://huggingface.co/settings/tokens
    echo   2. Open "My Hugging Face Token.txt" in Notepad, paste your hf_...
    echo      token, and save.
    echo   3. Make sure you clicked Agree on BOTH:
    echo        https://huggingface.co/pyannote/segmentation-3.0
    echo        https://huggingface.co/pyannote/speaker-diarization-3.1
    echo   4. Double-click "1 Setup.bat" again to finish downloading models.
    echo ================================================================
    pause
    exit /b 1
)

echo.
echo ================================================================
echo ALL SET! Audio Transcriber is ready.
echo   - Pick your folders : 2 Choose Folders.bat
echo   - Start watching    : 3 Start Transcriber.bat
echo ================================================================
pause
