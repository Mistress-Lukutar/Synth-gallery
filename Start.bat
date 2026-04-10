@echo off
chcp 65001 >nul
echo ╔══════════════════════════════════════════════════════════════╗
echo ║                Synth Gallery - Startup Script                ║
echo ╚══════════════════════════════════════════════════════════════╝
echo.
echo   Time:    %date% %time%
echo.

:: ═════════════════════════════════════════════════════════════════
:: CONFIGURATION - Edit this line to change the base URL path
:: ═════════════════════════════════════════════════════════════════
:: 
:: Examples:
::   set BASE_PATH=                    (empty = root path:  http://localhost:8008/)
::   set BASE_PATH=synth               (subfolder:          http://localhost:8008/synth/)
::   set BASE_PATH=gallery             (subfolder:          http://localhost:8008/gallery/)
::   set BASE_PATH=photos/v2           (nested path:        http://localhost:8008/photos/v2/)
::
:: NOTE: Do NOT add leading or trailing slashes!
:: ═════════════════════════════════════════════════════════════════

set BASE_PATH=synth
set SYNTH_ENV=development

:: ═════════════════════════════════════════════════════════════════
:: ADVANCED CONFIGURATION
:: ═════════════════════════════════════════════════════════════════
set PORT=8008
set HOST=0.0.0.0

set BACKUP_ROTATION_COUNT=3

:: Set the environment variable for the application
set SYNTH_BASE_URL=%BASE_PATH%

:: Display configuration
echo ════════════════════════════════════════════════════════════════
echo  Configuration:
echo ════════════════════════════════════════════════════════════════
if "%BASE_PATH%"=="" (
    echo   Base URL:     / (root)
    echo   Full URL:     http://localhost:%PORT%/
) else (
    echo   Base URL:     /%BASE_PATH%/
    echo   Full URL:     http://localhost:%PORT%/%BASE_PATH%/
)
echo   Port:         %PORT%
echo   Host:         %HOST%
echo ════════════════════════════════════════════════════════════════
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
echo ════════════════════════════════════════════════════════════════
echo  Starting server...
echo ════════════════════════════════════════════════════════════════

if "%BASE_PATH%"=="" (
    echo   Gallery:    http://localhost:%PORT%/
    echo   Login:      http://localhost:%PORT%/login
) else (
    echo   Gallery:    http://localhost:%PORT%/%BASE_PATH%/
    echo   Login:      http://localhost:%PORT%/%BASE_PATH%/login
)
echo.
echo   Press Ctrl+C to stop the server
echo ════════════════════════════════════════════════════════════════
echo.

:: Start the server
uvicorn app.main:app --reload --port %PORT% --host %HOST%

:: Pause if server stops
echo.
echo Server stopped.
pause
