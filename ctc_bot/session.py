"""Authenticated RaceClocker session.

The admin login is a plain HTML form post - no CSRF token, no hidden fields,
no JavaScript step::

    POST https://raceclocker.com/Login.php
        fld_email=...&fld_password=...

Authentication is then carried by cookies, so a single ``requests.Session``
covers the whole run. No browser and no automation tooling is involved.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import requests

from . import credentials as creds

BASE_URL = "https://raceclocker.com"
LOGIN_URL = f"{BASE_URL}/Login.php"
EVENTS_URL = f"{BASE_URL}/My_Events.php"

USER_AGENT = "CTC_bot/0.1 (club results tracker)"

# The login page shows this field; a response still containing it means the
# credentials were rejected and we were bounced back to the form.
_LOGIN_FORM_RE = re.compile(r'name=["\']fld_password["\']', re.I)


class LoginError(RuntimeError):
    """Raised when the admin login is rejected or unreachable."""


@dataclass
class LoginResult:
    ok: bool
    final_url: str
    detail: str = ""


def is_logged_out(html: str) -> bool:
    """True if the page is the login form rather than authenticated content."""
    return bool(_LOGIN_FORM_RE.search(html))


def login(
    username: str | None = None,
    password: str | None = None,
    *,
    session: requests.Session | None = None,
    timeout: int = 30,
) -> requests.Session:
    """Log in and return a session carrying the auth cookies.

    Credentials default to the encrypted local store, so normal use is simply
    ``session = login()`` with no secrets anywhere in code or config.
    """
    if username is None or password is None:
        stored = creds.load()
        username = username or stored.username
        password = password or stored.password

    http = session or requests.Session()
    http.headers.update({"User-Agent": USER_AGENT})

    try:
        response = http.post(
            LOGIN_URL,
            data={"fld_email": username, "fld_password": password},
            timeout=timeout,
            allow_redirects=True,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise LoginError(f"Could not reach RaceClocker: {exc}") from exc

    if is_logged_out(response.text):
        raise LoginError(
            "Login rejected. Check the email and password, then re-run:\n"
            "    ctc login"
        )

    return http


def check() -> LoginResult:
    """Verify the stored credentials by logging in and loading the event list.

    Never raises for a bad password - returns a result so callers (and the
    setup script) can report cleanly.
    """
    try:
        http = login()
    except (LoginError, creds.CredentialError) as exc:
        return LoginResult(False, LOGIN_URL, str(exc))

    try:
        response = http.get(EVENTS_URL, timeout=30)
        response.raise_for_status()
    except requests.RequestException as exc:
        return LoginResult(False, EVENTS_URL, f"Logged in, but could not load the event list: {exc}")

    if is_logged_out(response.text):
        return LoginResult(False, response.url, "Session was not accepted for the event list.")

    return LoginResult(True, response.url, "Logged in and reached the admin event list.")


def fetch(url: str, *, session: requests.Session | None = None, timeout: int = 30) -> str:
    """Fetch an authenticated page, logging in first if needed."""
    http = session or login()
    response = http.get(url, timeout=timeout)
    response.raise_for_status()
    if is_logged_out(response.text):
        raise LoginError(f"Session expired or not authenticated for {url}")
    return response.text


def fetch_event_list(*, session: requests.Session | None = None) -> str:
    """Raw HTML of the admin event list."""
    return fetch(EVENTS_URL, session=session)
