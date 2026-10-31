@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
py -3.12 -c "import sys; assert sys.version_info[:2] == (3, 12)" >nul 2>&1
if not errorlevel 1 goto use_py
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" goto use_local
echo Python 3.12 was not found. Keep this window open and report this message.
pause
exit /b 1
:use_py
py -3.12 repair_windows.py
goto finish
:use_local
"%LOCALAPPDATA%\Programs\Python\Python312\python.exe" repair_windows.py
:finish
if errorlevel 1 pause
endlocal
