@echo off
cd /d "%~dp0"
python pm.py serve --open
if errorlevel 1 pause
