@echo off
setlocal
rem ---------------------------------------------------------------------------
rem  CTC_bot launcher.
rem
rem    ctc              list the available commands
rem    ctc login        store the RaceClocker login
rem    ctc doctor       check TLS, credentials and stored events
rem    ctc test         run the test suite
rem    ctc events       list stored events
rem
rem  Prefers the checksum-verified pixi in ..\tools, and falls back to any pixi
rem  on PATH so this still works if the applet folder is moved.
rem ---------------------------------------------------------------------------

set "PIXI=%~dp0..\tools\pixi.exe"
if not exist "%PIXI%" set "PIXI=pixi"

pushd "%~dp0"

if "%~1"=="" (
    echo CTC_bot commands:
    echo.
    "%PIXI%" task list
    echo.
    echo Usage: ctc ^<command^>   e.g.  ctc login
    popd
    exit /b 0
)

"%PIXI%" run %*
set "CODE=%ERRORLEVEL%"
popd
exit /b %CODE%
