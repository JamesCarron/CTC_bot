#!/usr/bin/env python
"""Check that everything CTC_bot needs is working.

Run:  pixi run doctor

Reports on TLS, stored credentials, the RaceClocker login, and locally stored
events - so a failure points at the actual cause rather than surfacing as an
obscure traceback later.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ctc_bot  # noqa: E402,F401  (import applies the TLS fix)
from ctc_bot import credentials as creds  # noqa: E402
from ctc_bot import store  # noqa: E402

OK = "  OK   "
BAD = " FAIL  "
SKIP = " SKIP  "


def check_tls() -> bool:
    try:
        import requests

        requests.get("https://raceclocker.com/Login.php", timeout=20).raise_for_status()
    except Exception as exc:
        print(f"{BAD} TLS: {type(exc).__name__}: {str(exc)[:110]}")
        print("       Is `truststore` installed? See requirements.txt.")
        return False
    print(f"{OK} TLS: verified against the Windows certificate store")
    return True


def check_credentials() -> bool:
    if not creds.available():
        print(f"{BAD} Credentials: no secure backend (install pywin32)")
        return False
    if not creds.exists():
        print(f"{SKIP} Credentials: none stored yet - run `pixi run login`")
        return False
    try:
        stored = creds.load()
    except creds.CredentialError as exc:
        print(f"{BAD} Credentials: {str(exc).splitlines()[0]}")
        return False
    print(f"{OK} Credentials: stored for {stored.username}")
    print(f"       {creds.default_path()}")
    return True


def check_login() -> bool:
    from ctc_bot import session

    result = session.check()
    print(f"{OK if result.ok else BAD} Login: {result.detail.splitlines()[0]}")
    return result.ok


def check_events() -> bool:
    events = store.load_all()
    if not events:
        print(f"{SKIP} Events: none stored locally yet")
        return False
    print(f"{OK} Events: {len(events)} stored")
    for stored_event in events:
        flag = "" if stored_event.confident else "  (needs review)"
        print(
            f"       {stored_event.code}  {stored_event.date_text or '?':<27}"
            f"{stored_event.race_type:<12}{len(stored_event.event.results):>3} athletes{flag}"
        )
    return True


def main() -> int:
    print("CTC_bot doctor")
    print("=" * 60)

    tls_ok = check_tls()
    have_creds = check_credentials()

    if tls_ok and have_creds:
        check_login()
    else:
        print(f"{SKIP} Login: skipped (needs working TLS and stored credentials)")

    check_events()

    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
