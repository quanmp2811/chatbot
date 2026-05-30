@echo off
setlocal
cd /d %~dp0

REM Use root venv: D:\Do-an-tot-nghiepv5\.venv
set "PYTHON_EXE=%~dp0..\.venv\Scripts\python.exe"

if not exist "%PYTHON_EXE%" (
    echo Missing virtual environment at ..\.venv
    echo Create it with: py -3.10 -m venv ..\.venv
    pause
    exit /b 1
)

"%PYTHON_EXE%" -m pip install --upgrade pip
"%PYTHON_EXE%" -m pip install -r requirements.txt

REM Always run uvicorn via the same interpreter
"%PYTHON_EXE%" -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

endlocal
