@echo off
setlocal enabledelayedexpansion
title AI Inventory Manager

:: ─────────────────────────────────────────────────────────────────
::  Always run from the folder where this .bat file lives,
::  no matter where the user double-clicks it from.
:: ─────────────────────────────────────────────────────────────────
cd /d "%~dp0"

echo.
echo  =====================================================
echo         AI INVENTORY MANAGER  ^|  Billing Software
echo  =====================================================
echo.

:: ─────────────────────────────────────────────────────────────────
::  STEP 1 — Find Python 3 (tries PATH first, then common installs)
:: ─────────────────────────────────────────────────────────────────
set PYTHON_EXE=

:: Try 'python' from PATH
where python >nul 2>&1
if %errorlevel% equ 0 (
    for /f "delims=" %%i in ('where python') do (
        if not defined PYTHON_EXE (
            "%%i" --version 2>&1 | findstr /r "Python 3\." >nul 2>&1
            if !errorlevel! equ 0 set "PYTHON_EXE=%%i"
        )
    )
)

:: Try 'python3' from PATH (common on machines with both Python 2 & 3)
if not defined PYTHON_EXE (
    where python3 >nul 2>&1
    if %errorlevel% equ 0 (
        for /f "delims=" %%i in ('where python3') do (
            if not defined PYTHON_EXE set "PYTHON_EXE=%%i"
        )
    )
)

:: Try common Windows Store / default install locations
if not defined PYTHON_EXE (
    for %%D in (
        "%LOCALAPPDATA%\Programs\Python"
        "%ProgramFiles%\Python3*"
        "%ProgramFiles(x86)%\Python3*"
        "C:\Python3*"
    ) do (
        for /d %%P in (%%D) do (
            if not defined PYTHON_EXE (
                if exist "%%P\python.exe" (
                    "%%P\python.exe" --version 2>&1 | findstr /r "Python 3\." >nul 2>&1
                    if !errorlevel! equ 0 set "PYTHON_EXE=%%P\python.exe"
                )
            )
        )
    )
)

if not defined PYTHON_EXE (
    echo.
    echo  [ERROR] Python 3 was not found on this system.
    echo  Please install Python 3 from https://www.python.org/downloads/
    echo  Make sure to check "Add Python to PATH" during installation.
    echo.
    pause
    exit /b 1
)

echo  [OK]    Python found: %PYTHON_EXE%

:: ─────────────────────────────────────────────────────────────────
::  STEP 2 — Validate / recreate the virtual environment
::           A venv copied from another machine has broken absolute
::           paths baked in, so we detect that and rebuild it.
:: ─────────────────────────────────────────────────────────────────
set VENV_DIR=%~dp0venv
set RECREATE=0

if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo  [INFO]  No virtual environment found. Creating one...
    set RECREATE=1
) else (
    :: Test if the venv python actually runs (broken if paths changed)
    "%VENV_DIR%\Scripts\python.exe" --version >nul 2>&1
    if !errorlevel! neq 0 (
        echo  [WARN]  Virtual environment is broken ^(likely copied from another PC^).
        echo  [INFO]  Deleting and recreating virtual environment...
        rd /s /q "%VENV_DIR%"
        set RECREATE=1
    )
)

if !RECREATE! equ 1 (
    echo  [INFO]  Creating virtual environment...
    "%PYTHON_EXE%" -m venv "%VENV_DIR%"
    if !errorlevel! neq 0 (
        echo.
        echo  [ERROR] Failed to create virtual environment.
        echo  Try running this script as Administrator.
        echo.
        pause
        exit /b 1
    )
    echo  [INFO]  Installing required packages (this may take a few minutes)...
    "%VENV_DIR%\Scripts\python.exe" -m pip install --upgrade pip --quiet
    "%VENV_DIR%\Scripts\python.exe" -m pip install -r "%~dp0requirements.txt"
    if !errorlevel! neq 0 (
        echo.
        echo  [ERROR] Package installation failed.
        echo  Check your internet connection and try again.
        echo.
        pause
        exit /b 1
    )
    echo  [OK]    Packages installed successfully.
) else (
    echo  [OK]    Virtual environment is ready.
)

:: ─────────────────────────────────────────────────────────────────
::  STEP 3 — Start Flask, then open browser after a short delay
:: ─────────────────────────────────────────────────────────────────
echo.
echo  [INFO]  Starting Flask server on http://127.0.0.1:5000
echo  [INFO]  Keep this window open. Closing it will stop the app.
echo.
echo  ─────────────────────────────────────────────────────
echo   Press Ctrl+C to stop the server when you are done.
echo  ─────────────────────────────────────────────────────
echo.

:: Open the browser after a 2-second delay (runs in background)
start "" cmd /c "timeout /t 2 /nobreak >nul & start http://127.0.0.1:5000"

:: Run the app using the venv python (explicit path — no activation needed)
"%VENV_DIR%\Scripts\python.exe" "%~dp0app.py"

echo.
echo  [INFO]  Server stopped.
pause
