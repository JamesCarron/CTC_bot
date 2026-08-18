@echo off
setlocal
rem ---------------------------------------------------------------------------
rem  Launch the CTC_bot dashboard.
rem
rem  Builds the page from the local store, starts the local server, and opens
rem  it in your browser. The server exists so the claim form can write back to
rem  the identity file; it binds to localhost only.
rem
rem  Close this window, or press Ctrl+C, to stop the server.
rem ---------------------------------------------------------------------------

title CTC_bot dashboard

set "PIXI=%~dp0..\tools\pixi.exe"
if not exist "%PIXI%" set "PIXI=pixi"

pushd "%~dp0"

echo Starting the CTC_bot dashboard...
echo.

"%PIXI%" run dashboard %*
set "CODE=%ERRORLEVEL%"

if not "%CODE%"=="0" (
    echo.
    echo The dashboard exited with code %CODE%.
    echo If no events are stored yet, run:  ctc backfill
    echo.
    pause
)

popd
exit /b %CODE%
