@echo off
setlocal
rem ---------------------------------------------------------------------------
rem  Set the RaceClocker password on the server (tri.jamescarron.cloud).
rem
rem  The Wed/Fri sweep and the race-night polling need it; the dashboard itself
rem  does not. Until it is set, the site works fully but the refresh logs a
rem  credential error.
rem
rem  The password is typed hidden, sent to the server over the SSH connection's
rem  stdin, and only ever exists there in an environment variable. It is never
rem  passed as a command argument (visible in `ps` to every user on the box),
rem  never written to a plaintext file, and never enters shell history on
rem  either machine.
rem
rem  A fingerprint of the value is compared against what the running container
rem  actually holds, so a value altered in transit is reported as exactly that
rem  rather than as a wrong password.
rem
rem  Afterwards the re-encrypted secret is copied back here and pushed, so the
rem  infra repo stays the source of truth rather than drifting from the server.
rem ---------------------------------------------------------------------------

title Set RaceClocker password on the server

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\set_server_password.ps1" %*
set "CODE=%ERRORLEVEL%"

echo.
echo ---------------------------------------------------------------------------
if "%CODE%"=="0" (
    echo  Done. The refresh can now log in to RaceClocker.
) else (
    echo  FAILED with code %CODE%. Nothing on the server was left half-changed:
    echo  the script restores the previous encrypted secret if it cannot finish.
    echo.
    echo    1  could not reach the server, or the value was altered in transit
    echo    2  stored and received, but RaceClocker rejected the credentials
    echo    3  stored, but the container did not pick it up
)
echo ---------------------------------------------------------------------------
echo.
echo  Review the output above, then press any key to close.
pause >nul

exit /b %CODE%
