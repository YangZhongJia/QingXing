@echo off
rem ---------------------------------------------------------------------------
rem Build the standalone Windows executable with PySide6's deployment tool.
rem User data (%APPDATA%\DesktopHealthReminder) is never touched.
rem ---------------------------------------------------------------------------
setlocal

cd /d "%~dp0.."
if not defined PROCESSOR_ARCHITECTURE set "PROCESSOR_ARCHITECTURE=AMD64"

if exist "dist\DesktopHealthReminder" rmdir /s /q "dist\DesktopHealthReminder"

pyside6-deploy app.py --force
if errorlevel 1 (
    echo [error] PySide6 deployment failed
    exit /b 1
)

echo.
echo Build finished: dist\DesktopHealthReminder.exe
exit /b 0
