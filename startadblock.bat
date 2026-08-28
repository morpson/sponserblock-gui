@echo off
setlocal
cd /d "%~dp0"
"%~dp0.venv\Scripts\pythonw.exe" "%~dp0scripts\gui_launcher.py"
