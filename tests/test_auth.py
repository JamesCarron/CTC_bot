"""Tests for the session login.

This module is now the only thing between the internet and the club's data -
Traefik's basicauth was removed when the login page replaced it - so the cases
that matter are the ones where a mistake would let somebody in.
"""

import time

import pytest

from ctc_bot import auth


@pytest.fixture(autouse=True)
def isolated_secret(tmp_path, monkeypatch):
    """Never touch the real signing key, and never inherit one from the env."""
    monkeypatch.setattr(auth, "_SECRET_FILE", tmp_path / ".session_secret")
    monkeypatch.delenv(auth.ENV_SESSION_SECRET, raising=False)
    monkeypatch.setenv(auth.ENV_PASSWORD, "test-password")


# ---- whether auth applies at all ----------------------------------------


def test_disabled_without_a_password(monkeypatch):
    """The local install runs on 127.0.0.1 with no password and no login."""
    monkeypatch.delenv(auth.ENV_PASSWORD, raising=False)
    assert not auth.is_enabled()


def test_enabled_with_a_password():
    assert auth.is_enabled()


def test_no_password_accepts_nothing(monkeypatch):
    """Auth being off must not mean "any password works" for anything that asks."""
    monkeypatch.delenv(auth.ENV_PASSWORD, raising=False)
    assert not auth.check_password("")
    assert not auth.check_password("anything")


# ---- the password --------------------------------------------------------


def test_correct_password_accepted():
    assert auth.check_password("test-password")


@pytest.mark.parametrize("wrong", ["", "Test-Password", "test-passwor", "test-password "])
def test_wrong_passwords_rejected(wrong):
    assert not auth.check_password(wrong)


def test_comparison_is_constant_time():
    """== would leak the length and the matching prefix through timing."""
    import inspect

    source = inspect.getsource(auth.check_password)
    assert "compare_digest" in source


# ---- session tokens ------------------------------------------------------


def test_token_round_trips():
    assert auth.verify_token(auth.make_token())


def test_expired_token_rejected():
    past = time.time() - (auth.SESSION_HOURS * 3600) - 60
    assert not auth.verify_token(auth.make_token(now=past))


def test_token_expiring_shortly_still_valid():
    nearly = time.time() - (auth.SESSION_HOURS * 3600) + 120
    assert auth.verify_token(auth.make_token(now=nearly))


@pytest.mark.parametrize("forged", [
    "",
    None,
    "garbage",
    "9999999999.deadbeef",              # right shape, wrong signature
    "9999999999",                       # no signature at all
    "notanumber.deadbeef",
])
def test_forged_tokens_rejected(forged):
    assert not auth.verify_token(forged)


def test_extending_the_expiry_invalidates_the_signature():
    """The obvious attack: keep the signature, push the date out."""
    token = auth.make_token()
    expiry, _, signature = token.rpartition(".")
    tampered = f"{int(expiry) + 86400}.{signature}"
    assert not auth.verify_token(tampered)


def test_a_token_signed_with_another_key_is_rejected(tmp_path, monkeypatch):
    token = auth.make_token()
    monkeypatch.setattr(auth, "_SECRET_FILE", tmp_path / "different")
    assert not auth.verify_token(token)


# ---- the signing key -----------------------------------------------------


def test_secret_persists_across_calls():
    """A key regenerated per boot would log the club out on every redeploy."""
    assert auth.session_secret() == auth.session_secret()


def test_secret_file_is_created_private():
    import os
    import stat

    auth.session_secret()
    mode = stat.S_IMODE(os.stat(auth._SECRET_FILE).st_mode)
    if os.name != "nt":  # Windows does not carry POSIX modes
        assert mode == 0o600
    assert auth._SECRET_FILE.stat().st_size >= 32


def test_environment_secret_wins(monkeypatch):
    monkeypatch.setenv(auth.ENV_SESSION_SECRET, "from-the-environment")
    assert auth.session_secret() == b"from-the-environment"


# ---- lockout -------------------------------------------------------------


def test_lockout_after_repeated_failures():
    attempts = auth.Attempts(limit=3, lockout=300)
    assert attempts.locked_for("1.2.3.4") == 0
    for _ in range(3):
        attempts.record_failure("1.2.3.4")
    assert attempts.locked_for("1.2.3.4") > 0


def test_lockout_is_per_client():
    """One person guessing must not lock everybody else out."""
    attempts = auth.Attempts(limit=2, lockout=300)
    attempts.record_failure("1.2.3.4")
    attempts.record_failure("1.2.3.4")
    assert attempts.locked_for("1.2.3.4") > 0
    assert attempts.locked_for("5.6.7.8") == 0


def test_lockout_expires():
    attempts = auth.Attempts(limit=2, lockout=60)
    old = time.time() - 120
    attempts.record_failure("1.2.3.4", now=old)
    attempts.record_failure("1.2.3.4", now=old)
    assert attempts.locked_for("1.2.3.4") == 0


def test_success_clears_the_count():
    attempts = auth.Attempts(limit=3, lockout=300)
    attempts.record_failure("1.2.3.4")
    attempts.record_failure("1.2.3.4")
    attempts.clear("1.2.3.4")
    attempts.record_failure("1.2.3.4")
    assert attempts.locked_for("1.2.3.4") == 0


# ---- the routing that uses all of the above ------------------------------


def test_server_gates_every_route_except_health_and_login():
    """A route added without a gate would be silently public."""
    from pathlib import Path

    source = (Path(__file__).parent.parent / "ctc_bot" / "server.py").read_text(encoding="utf-8")

    # GET and POST both check before doing anything.
    assert source.count("if not self._authenticated():") == 2
    # Health is open on purpose - Docker and Uptime Kuma cannot hold a session.
    assert '/api/health' in source
    # API callers get JSON, browsers get the login page.
    assert 'if path.startswith("/api/"):' in source


def test_login_redirect_cannot_be_used_as_an_open_redirect():
    """?next= is attacker-controlled; it must never leave this site."""
    from pathlib import Path

    source = (Path(__file__).parent.parent / "ctc_bot" / "server.py").read_text(encoding="utf-8")
    assert 'if not nxt.startswith("/") or nxt.startswith("//"):' in source


def test_session_cookie_carries_the_right_flags():
    from pathlib import Path

    source = (Path(__file__).parent.parent / "ctc_bot" / "server.py").read_text(encoding="utf-8")
    for flag in ("HttpOnly", "SameSite=Lax", "Secure", "Path=/"):
        assert flag in source


def test_lockout_keys_on_the_real_visitor_not_the_proxy():
    """Behind Cloudflare every request comes from the proxy.

    Keying on the socket address would put the whole internet in one bucket and
    lock everybody out together.
    """
    from pathlib import Path

    source = (Path(__file__).parent.parent / "ctc_bot" / "server.py").read_text(encoding="utf-8")
    assert 'X-Forwarded-For' in source
