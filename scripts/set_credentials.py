#!/usr/bin/env python
"""Store the RaceClocker admin login securely on this machine.

Run:

    python scripts/set_credentials.py            # store (and verify) credentials
    python scripts/set_credentials.py --check    # test what is already stored
    python scripts/set_credentials.py --delete   # remove the stored credentials

The password is typed without echo, is never written to disk in plaintext, and
is never printed back. It is encrypted with Windows DPAPI, which binds it to
your Windows user account, and stored outside this repository so it cannot be
committed by accident.
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ctc_bot import credentials as creds  # noqa: E402
from ctc_bot import session  # noqa: E402


def _prompt_credentials() -> tuple[str, str]:
    """Ask for the login interactively, with the password hidden."""
    print("RaceClocker admin login")
    print("-" * 40)

    username = input("Email address: ").strip()
    if not username:
        raise SystemExit("No email address entered - nothing stored.")

    password = getpass.getpass("Password (hidden): ")
    if not password:
        raise SystemExit("No password entered - nothing stored.")

    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        raise SystemExit("Passwords did not match - nothing stored.")

    return username, password


def _report(result: session.LoginResult) -> int:
    if result.ok:
        print(f"  OK  {result.detail}")
        return 0
    print(f"  FAILED  {result.detail}")
    return 1


def cmd_store() -> int:
    if not creds.available():
        print("No secure credential backend is available.")
        print("Install pywin32 to enable Windows DPAPI encryption:")
        print("    python -m pip install pywin32")
        print("Refusing to store credentials in plaintext.")
        return 1

    if creds.exists():
        print(f"Credentials already stored at {creds.default_path()}")
        if input("Overwrite? [y/N] ").strip().lower() not in {"y", "yes"}:
            print("Left unchanged.")
            return 0

    username, password = _prompt_credentials()

    path = creds.store(username, password)
    print()
    print(f"Stored at   {path}")
    print(f"Protected by {creds.backend_name()}")
    print()

    print("Verifying against RaceClocker...")
    return _report(session.check())


def cmd_check() -> int:
    if not creds.exists():
        print(f"No credentials stored at {creds.default_path()}")
        print("Run:  python scripts/set_credentials.py")
        return 1

    stored = creds.load()
    print(f"Stored at   {creds.default_path()}")
    print(f"Protected by {creds.backend_name()}")
    print(f"Username    {stored.username}")
    print()
    print("Verifying against RaceClocker...")
    return _report(session.check())


def cmd_delete() -> int:
    if creds.delete():
        print("Stored credentials removed.")
        return 0
    print("Nothing stored, so nothing to remove.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true", help="verify the stored credentials")
    group.add_argument("--delete", action="store_true", help="remove the stored credentials")
    args = parser.parse_args()

    if args.check:
        return cmd_check()
    if args.delete:
        return cmd_delete()
    return cmd_store()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nCancelled - nothing stored.")
        raise SystemExit(130)
