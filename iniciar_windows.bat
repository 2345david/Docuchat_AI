@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\pythonw.exe (echo Ejecuta primero instalar_windows.bat & pause & exit /b 1)
start "" .venv\Scripts\pythonw.exe main.py
