@echo off
REM Build the portable "HCW Markdown Tool.exe" with PyInstaller.
REM Output: dist\HCW Markdown Tool.exe  (single portable file, no Python needed)
setlocal
set "PY=%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe"
cd /d "%~dp0"

"%PY%" -m PyInstaller --noconfirm --onefile --windowed --name "HCW Markdown Tool" ^
  --collect-all ttkbootstrap --collect-all pymupdf4llm --collect-all pymupdf ^
  --add-data "tessdata;tessdata" ^
  --exclude-module torch --exclude-module torchvision --exclude-module torchaudio ^
  --exclude-module cv2 --exclude-module scipy --exclude-module pandas ^
  --exclude-module matplotlib --exclude-module pyarrow --exclude-module sklearn ^
  --exclude-module tensorflow --exclude-module sympy --exclude-module IPython ^
  --exclude-module notebook --exclude-module numba ^
  pdf_to_markdown.py

echo.
echo Done. The portable app is at: dist\HCW Markdown Tool.exe
endlocal
