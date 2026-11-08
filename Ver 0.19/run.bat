@echo off
rem ============================================================
rem  True MultiExtractTool launcher
rem
rem  NOTE: this file is intentionally pure ASCII and CRLF.
rem  cmd.exe reads .bat files using the OEM codepage (GBK on Chinese
rem  Windows), NOT UTF-8. Non-ASCII text here gets mangled into garbage
rem  that cmd then tries to execute -- it can even swallow the following
rem  keyword (observed: "else" became "lse"). All localized messages live
rem  in Python (me/ui.py), which handles its own encoding.
rem
rem  Usage: double-click, or drag a game folder / exe onto this file.
rem ============================================================

rem Make the console and Python agree on UTF-8, otherwise a typed or
rem piped Chinese path gets decoded with the wrong codepage.
chcp 65001 >nul 2>nul
set "PYTHONIOENCODING=utf-8"

rem Delayed expansion lets us read the real exit code with !ERRORLEVEL!
rem (safe everywhere). The %ERRORLEVEL% form would be expanded when the
rem line is parsed, which gives stale values inside parenthesised blocks.
setlocal enabledelayedexpansion
cd /d "%~dp0"

if not exist "%~dp0main.py" (
    echo.
    echo [x] main.py not found next to this launcher.
    echo     Expected: %~dp0main.py
    echo.
    pause >nul
    exit /b 1
)

rem Find a Python interpreter: prefer the "py" launcher, fall back to "python".
set "PY="
where py >nul 2>nul
if not errorlevel 1 set "PY=py -3"
if not defined PY (
    where python >nul 2>nul
    if not errorlevel 1 set "PY=python"
)

if not defined PY (
    echo.
    echo [x] Python not found.
    echo     Please install Python 3.8 or newer:
    echo         https://www.python.org/downloads/
    echo     Remember to tick "Add Python to PATH" during setup.
    echo.
    pause >nul
    exit /b 1
)

if "%~1"=="" (
    %PY% "%~dp0main.py"
) else (
    %PY% "%~dp0main.py" %*
)
set "RC=!ERRORLEVEL!"

echo.
pause >nul

rem Pass the tool's own exit code through, so run.bat stays script-friendly.
exit /b !RC!
