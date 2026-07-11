@echo off
echo ================================================================
echo                  Synth Gallery - Startup Script
echo ================================================================
echo.
echo   Time:    %date% %time%
echo.

:: ----------------------------------------------------------------
:: CONFIGURATION - Edit this line to change the base URL path
:: ----------------------------------------------------------------
:: 
:: Examples:
::   set BASE_PATH=                    (empty = root path:  http://localhost:8008/)
::   set BASE_PATH=synth               (subfolder:          http://localhost:8008/synth/)
::   set BASE_PATH=gallery             (subfolder:          http://localhost:8008/gallery/)
::   set BASE_PATH=photos/v2           (nested path:        http://localhost:8008/photos/v2/)
::
:: NOTE: Do NOT add leading or trailing slashes!
:: ----------------------------------------------------------------

set BASE_PATH=synth
set SYNTH_ENV=development

:: ----------------------------------------------------------------
:: ADVANCED CONFIGURATION
:: ----------------------------------------------------------------
set PORT=8008
set HOST=0.0.0.0

set BACKUP_ROTATION_COUNT=3

:: ----------------------------------------------------------------
:: JPEG XL EXPERIMENTAL STORAGE
:: ----------------------------------------------------------------
:: Uncomment the next line to transcode new image uploads to lossless JPEG XL.
:: Existing files and videos are not affected.
set USE_JXL=true

:: JPEG fallback quality for browsers without JXL support (1-100).
set JXL_FALLBACK_QUALITY=85

:: Use lossless JPEG transcode for JPEG sources when USE_JXL is enabled.
:: Disable this to re-encode JPEGs as lossless pixel data instead.
set JXL_LOSSLESS_TRANSCODE_JPEG=true

:: Encoder effort: 1 (fastest, larger) to 9 (slowest, smaller). Default 7.
set JXL_EFFORT=7

:: Encoder threads: -1 lets the encoder decide, 0 disables threading.
set JXL_THREADS=-1

:: Decoding speed / progressive rendering: 0 best progressive, 4 fastest decode.
set JXL_DECODING_SPEED=0

:: Set the environment variable for the application
set SYNTH_BASE_URL=%BASE_PATH%

:: Display configuration
echo ----------------------------------------------------------------
echo  Configuration:
echo ----------------------------------------------------------------
if "%BASE_PATH%"=="" (
    echo   Base URL:     / (root)
    echo   Full URL:     http://localhost:%PORT%/
) else (
    echo   Base URL:     /%BASE_PATH%/
    echo   Full URL:     http://localhost:%PORT%/%BASE_PATH%/
)
echo   Port:         %PORT%
echo   Host:         %HOST%
echo ----------------------------------------------------------------
echo.

:: Check/create virtual environment
if not exist ".venv" (
    echo [1/3] Creating virtual environment in .venv\
    python -m venv .venv
    if errorlevel 1 (
        echo ERROR: Failed to create virtual environment
        pause
        exit /b 1
    )
    echo      OK: Virtual environment created
    echo.
) else (
    echo [1/3] Virtual environment found: .venv\
)

:: Activate virtual environment
echo [2/3] Activating virtual environment...
call .venv\Scripts\activate.bat
if errorlevel 1 (
    echo ERROR: Failed to activate virtual environment
    pause
    exit /b 1
)
echo      OK: Virtual environment activated

:: ----------------------------------------------------------------
:: JPEG XL TOOLS (cjxl / djxl)
:: ----------------------------------------------------------------
:: The application can use the official libjxl CLI for progressive
:: JXL encoding. Check common locations and download if missing.
:: ----------------------------------------------------------------
set JXL_TOOLS_DIR=

where cjxl.exe >nul 2>nul
if %errorlevel% == 0 (
    echo      OK: cjxl.exe found in PATH
    goto jxl_done
)

if exist "C:\jxl-x64-windows-static\bin\cjxl.exe" (
    echo      OK: Found local JPEG XL tools at C:\jxl-x64-windows-static\bin
    set JXL_TOOLS_DIR=C:\jxl-x64-windows-static
    set PATH=%PATH%;C:\jxl-x64-windows-static\bin
    goto jxl_done
)

if exist ".venv\jxl-tools\bin\cjxl.exe" (
    echo      OK: Found project JPEG XL tools at .venv\jxl-tools
    set JXL_TOOLS_DIR=%CD%\.venv\jxl-tools
    set PATH=%PATH%;%CD%\.venv\jxl-tools\bin
    goto jxl_done
)

echo      Downloading JPEG XL tools, please wait...
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $rel = Invoke-RestMethod -Uri 'https://api.github.com/repos/libjxl/libjxl/releases/latest' -TimeoutSec 60; $asset = $rel.assets | Where-Object { $_.name -like '*x64-windows-static*.zip' } | Select-Object -First 1; if (-not $asset) { throw 'No Windows static asset found' }; Invoke-WebRequest -Uri $asset.browser_download_url -OutFile '.venv\jxl-tools.zip' -TimeoutSec 300; Expand-Archive -Path '.venv\jxl-tools.zip' -DestinationPath '.venv\jxl-tools-tmp' -Force; $src = Get-ChildItem -Path '.venv\jxl-tools-tmp' -Directory | Select-Object -First 1; Move-Item -Path $src.FullName -Destination '.venv\jxl-tools' -Force; Remove-Item -Path '.venv\jxl-tools.zip' -Force; Remove-Item -Path '.venv\jxl-tools-tmp' -Force } catch { Write-Error $_; exit 1 }"
if errorlevel 1 (
    echo WARNING: Failed to download JPEG XL tools. Progressive JXL encoding will be unavailable.
    goto jxl_done
)

set JXL_TOOLS_DIR=%CD%\.venv\jxl-tools
set PATH=%PATH%;%CD%\.venv\jxl-tools\bin
echo      OK: JPEG XL tools downloaded to .venv\jxl-tools

:jxl_done

:: Check Python version
echo      Python version:
for /f "tokens=*" %%a in ('python --version 2^>^&1') do echo        %%a

:: Install/update dependencies
echo [3/3] Installing dependencies...
echo      Pip version:
for /f "tokens=*" %%a in ('pip --version 2^>^&1') do echo        %%a
echo.
echo      Installing packages (this may take a minute)...
pip install -e .
if errorlevel 1 (
    echo ERROR: Failed to install dependencies
    pause
    exit /b 1
)
echo      OK: Dependencies installed

echo.
echo ----------------------------------------------------------------
echo  Starting server...
echo ----------------------------------------------------------------

if "%BASE_PATH%"=="" (
    echo   Gallery:    http://localhost:%PORT%/
    echo   Login:      http://localhost:%PORT%/login
) else (
    echo   Gallery:    http://localhost:%PORT%/%BASE_PATH%/
    echo   Login:      http://localhost:%PORT%/%BASE_PATH%/login
)
echo.
echo   Press Ctrl+C to stop the server
echo ----------------------------------------------------------------
echo.

:: Start the server
uvicorn app.main:app --reload --port %PORT% --host %HOST%

:: Pause if server stops
echo.
echo Server stopped.
pause
