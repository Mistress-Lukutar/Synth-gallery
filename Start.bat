@echo off
echo Starting...
venv\Scripts\uvicorn.exe app.main:app --reload --host 0.0.0.0 --port 8008
pause