@echo off
REM Launcher for the PDF to Markdown Converter.
REM Uses the local Python 3.14 install (python isn't on PATH via the Store alias).
setlocal
set "PY=%LOCALAPPDATA%\Python\pythoncore-3.14-64\pythonw.exe"
if not exist "%PY%" set "PY=%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe"
cd /d "%~dp0"
start "" "%PY%" "%~dp0pdf_to_markdown.py"
endlocal
