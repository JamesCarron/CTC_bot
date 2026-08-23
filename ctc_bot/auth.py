"""Session login for the dashboard.

This replaces Traefik's ``basicauth``, which worked but could only ever show the
browser's own credential dialog. Moving auth into the app means **this module is
now the only thing between the internet and the club's data**, so the pieces
that matter are deliberate:

* The password is compared with :func:`hmac.compare_digest`, not ``==``, so a
  wrong guess cannot be narrowed down by timing.
* The session cookie is an HMAC-signed expiry, not a random id in a table. There
  is no session store to grow, and a tampered or expired cookie fails on
  arithmetic rather than on a lookup.
* The signing key is persisted, so a container restart does not log the whole
  club out; it is generated once, with 0600 permissions, if absent.
* Failed attempts are counted per client and locked out briefly. Traefik's rate
  limit sits in front of this, but that protects the box, not the password.

With no password configured, authentication is **off** - that is how the local
install runs, bound to 127.0.0.1. The deployed instance always sets one.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import threading
import time
from pathlib import Path

ENV_PASSWORD = "CTC_SITE_PASSWORD"
ENV_SESSION_SECRET = "CTC_SESSION_SECRET"

COOKIE_NAME = "ctc_session"
SESSION_HOURS = int(os.environ.get("CTC_SESSION_HOURS", "720"))  # 30 days

# Brute-force dampening. Traefik's rate limit protects the server from load;
# this protects the one password from being guessed.
MAX_ATTEMPTS = 8
LOCKOUT_SECONDS = 300

_SECRET_FILE = Path(__file__).resolve().parent.parent / "data" / ".session_secret"


def site_password() -> str:
    return os.environ.get(ENV_PASSWORD, "")


def is_enabled() -> bool:
    """Whether the dashboard requires a login at all."""
    return bool(site_password())


def session_secret() -> bytes:
    """The key that signs session cookies.

    Persisted rather than regenerated per boot: a redeploy happens whenever the
    image changes, and logging every member out each time would train people to
    expect it and stop noticing a real one.
    """
    from_env = os.environ.get(ENV_SESSION_SECRET, "").strip()
    if from_env:
        return from_env.encode("utf-8")

    if _SECRET_FILE.exists():
        return _SECRET_FILE.read_bytes()

    generated = secrets.token_bytes(32)
    _SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
    # Created 0600, not chmod'd afterwards - there must be no window where the
    # signing key is world-readable.
    #
    # O_BINARY matters: without it Windows opens in text mode and rewrites every
    # 0x0A byte as \r\n. A random 32-byte key contains one about 12% of the time,
    # so the key read back differed from the key written, and sessions failed
    # signature verification seemingly at random. A no-op on Linux.
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0)
    handle = os.open(_SECRET_FILE, flags, 0o600)
    try:
        os.write(handle, generated)
    finally:
        os.close(handle)
    return generated


# ---- tokens --------------------------------------------------------------


def make_token(*, now: float | None = None) -> str:
    """A cookie value of ``<expiry>.<signature>``."""
    expiry = int((now or time.time()) + SESSION_HOURS * 3600)
    payload = str(expiry)
    signature = hmac.new(session_secret(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def verify_token(token: str | None, *, now: float | None = None) -> bool:
    """Whether a cookie is genuine and still valid."""
    if not token or "." not in token:
        return False
    payload, _, signature = token.rpartition(".")

    expected = hmac.new(session_secret(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return False

    try:
        expiry = int(payload)
    except ValueError:
        return False
    return expiry > (now or time.time())


def check_password(supplied: str) -> bool:
    """Constant-time comparison against the configured password."""
    configured = site_password()
    if not configured:
        return False
    return hmac.compare_digest(supplied.encode("utf-8"), configured.encode("utf-8"))


# ---- lockout -------------------------------------------------------------


class Attempts:
    """Per-client failure counter with a short lockout."""

    def __init__(self, *, limit: int = MAX_ATTEMPTS, lockout: int = LOCKOUT_SECONDS):
        self.limit = limit
        self.lockout = lockout
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def locked_for(self, client: str, *, now: float | None = None) -> int:
        """Seconds remaining before this client may try again."""
        now = now or time.time()
        with self._lock:
            recent = [t for t in self._failures.get(client, []) if now - t < self.lockout]
            self._failures[client] = recent
            if len(recent) < self.limit:
                return 0
            return int(self.lockout - (now - recent[-self.limit]))

    def record_failure(self, client: str, *, now: float | None = None) -> None:
        now = now or time.time()
        with self._lock:
            self._failures.setdefault(client, []).append(now)

    def clear(self, client: str) -> None:
        with self._lock:
            self._failures.pop(client, None)


attempts = Attempts()
