@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if not exist "%LOCALAPPDATA%\OmbreBrainShared\venv\Scripts\python.exe" goto missing
"%LOCALAPPDATA%\OmbreBrainShared\venv\Scripts\python.exe" upgrade_codex.py
if errorlevel 1 pause
exit /b
:missing
echo The existing OB Python environment was not found. Keep this window open and report this message.
pause
exit /b 1
