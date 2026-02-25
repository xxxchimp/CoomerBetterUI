@echo off
echo ======================================
echo Coomer BetterUI Build Script
echo ======================================
echo.

:: Check if Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python is not installed or not in PATH
    pause
    exit /b 1
)

:: Check if virtual environment exists
if not exist "venv\" (
    echo Creating virtual environment...
    python -m venv venv
)

:: Activate virtual environment
echo Activating virtual environment...
call venv\Scripts\activate.bat

:: Install/upgrade dependencies
echo Installing dependencies...
pip install --upgrade pip
pip install -r requirements.txt
pip install pyinstaller

:: Run build script
echo.
echo Building executable...
python build.py

if errorlevel 1 (
    echo.
    echo ERROR: Build failed
    pause
    exit /b 1
)

echo.
echo ======================================
echo Build completed successfully!
echo Executable location: dist\CoomerBetterUI.exe
echo ======================================
echo.
echo To create installer:
echo 1. Install Inno Setup from https://jrsoftware.org/isdl.php
echo 2. Open installer\setup.iss in Inno Setup
echo 3. Click Build ^> Compile
echo.
pause
