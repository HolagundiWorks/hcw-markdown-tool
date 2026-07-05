@echo off
REM Build the Windows installer "HCW-Markdown-Tool-Setup-x.x.x.exe" with Inno Setup.
REM Output: installer\HCW-Markdown-Tool-Setup-1.0.0.exe
setlocal
cd /d "%~dp0"

set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" (
  echo ERROR: Inno Setup 6 was not found.
  echo Install it with:  winget install JRSoftware.InnoSetup
  echo or download from:  https://jrsoftware.org/isdl.php
  exit /b 1
)

if not exist "dist\HCW Markdown Tool.exe" (
  echo ERROR: dist\HCW Markdown Tool.exe not found.
  echo Build the portable app first by running:  build_exe.bat
  exit /b 1
)

"%ISCC%" installer.iss
if errorlevel 1 (
  echo.
  echo Installer build FAILED.
  exit /b 1
)

echo.
echo Done. The installer is in the "installer" folder.
endlocal
