@echo off
setlocal enabledelayedexpansion
title AI INVENTORY MANAGER Startup Manager
cd /d "%~dp0"

echo ===================================================
echo             AI INVENTORY MANAGER
echo ===================================================
echo.

:: 1. Check for Python installation
where python >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not in your PATH.
    echo Please install Python from https://www.python.org/
    pause
    exit /b
)

:: 2. Setup/Verify virtual environment
set RECREATE_VENV=0
if not exist "venv\" (
    set RECREATE_VENV=1
) else (
    :: Verify if the virtual environment python works (copied venvs will fail with exit code != 0)
    venv\Scripts\python.exe --version >nul 2>&1
    if !errorlevel! neq 0 (
        echo [WARNING] Virtual environment is broken or was copied from another system.
        echo [INFO] Recreating virtual environment...
        rd /s /q "venv"
        set RECREATE_VENV=1
    )
)

if !RECREATE_VENV! equ 1 (
    echo [INFO] Setting up virtual environment...
    python -m venv venv
    if !errorlevel! neq 0 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b
    )
    echo [INFO] Installing required dependencies...
    call venv\Scripts\activate.bat
    venv\Scripts\python.exe -m pip install --upgrade pip
    venv\Scripts\python.exe -m pip install -r requirements.txt
) else (
    echo [INFO] Virtual environment found. Activating...
    call venv\Scripts\activate.bat
)

echo.
echo [INFO] Starting Flask Server...
echo [INFO] Keep this window open. Closing it will stop the system.
echo.

:: Launch app with virtual environment's Python executable explicitly
venv\Scripts\python.exe app.py

pause
