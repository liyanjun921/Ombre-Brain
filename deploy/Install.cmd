@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0.."
py -3.12 -c "import sys; assert sys.version_info[:2] == (3, 12)" >nul 2>&1
if not errorlevel 1 goto use_py
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" goto use_local
echo Installing Python 3.12 for the current Windows user...
winget install --id Python.Python.3.12 --exact --scope user --silent --disable-interactivity --accept-package-agreements --accept-source-agreements
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" goto use_local
echo Python setup is not ready. Install Python 3.12 from python.org, then reopen this file.
pause
exit /b 1
:use_py
py -3.12 deploy\windows_shared.py
goto finish
:use_local
"%LOCALAPPDATA%\Programs\Python\Python312\python.exe" deploy\windows_shared.py
:finish
if errorlevel 1 pause
endlocal
