@echo off
setlocal
rem ---------------------------------------------------------------------------
rem  Set the RaceClocker password on the server (tri.jamescarron.cloud).
rem
rem  The Wed/Fri refresh needs it; the dashboard itself does not. Until it is
rem  set, the site works fully but the refresh logs a credential error.
rem
rem  The password is typed hidden, sent to the server over the SSH connection's
rem  stdin, and only ever exists there in an environment variable. It is never
rem  passed as a command argument (visible in `ps` to every user on the box),
rem  never written to a plaintext file, and never enters shell history on
rem  either machine.
rem
rem  Afterwards the re-encrypted secret is copied back here and pushed, so the
rem  infra repo stays the source of truth rather than drifting from the server.
rem ---------------------------------------------------------------------------

title Set RaceClocker password on the server

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\set_server_password.ps1" %*
set "CODE=%ERRORLEVEL%"

if not "%CODE%"=="0" (
    echo.
    echo Failed with code %CODE%.
    echo.
    pause
)

exit /b %CODE%
