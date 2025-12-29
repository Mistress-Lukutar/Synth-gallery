@echo off
echo Starting Photo Gallery...
echo.

if not exist "venv" (
    echo Creating virtual environment...
    python -m venv venv
)

echo Activating virtual environment...
call venv\Scripts\activate.bat

echo Installing dependencies...
pip install -r requirements.txt

echo.
echo Access at http://localhost:8008
echo Press Ctrl+C to stop
echo.

uvicorn app.main:app --reload --port 8008 --host 0.0.0.0
pause
