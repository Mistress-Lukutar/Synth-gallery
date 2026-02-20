@echo off
chcp 65001 >nul
echo ╔══════════════════════════════════════════════════════════════╗
echo ║                Synth Gallery - Startup Script                ║
echo ╚══════════════════════════════════════════════════════════════╝
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

:: ═════════════════════════════════════════════════════════════════
:: ADVANCED CONFIGURATION
:: ═════════════════════════════════════════════════════════════════
set PORT=8008
set HOST=0.0.0.0

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
    echo Creating virtual environment...
    python -m venv .venv
    echo.
)

:: Activate virtual environment
echo Activating virtual environment...
call .venv\Scripts\activate.bat

:: Install/update dependencies
echo Installing dependencies...
pip install -r requirements.txt >nul 2>&1

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
