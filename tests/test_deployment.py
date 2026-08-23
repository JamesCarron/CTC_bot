"""Tests for the things the containerised deploy depends on.

None of this matters on a workstation; all of it matters the moment the write
API is reachable from the internet.
"""

import importlib
from datetime import datetime

import pytest

from ctc_bot import credentials as creds
from ctc_bot import scheduler as sched


# ---- credentials from the environment ------------------------------------


def test_environment_backend_is_used_when_set(monkeypatch):
    monkeypatch.setenv(creds.ENV_USERNAME, "team@example.com")
    monkeypatch.setenv(creds.ENV_PASSWORD, "s3cret")
    loaded = creds.load()
    assert loaded.username == "team@example.com"
    assert loaded.password == "s3cret"


def test_environment_wins_over_the_stored_file(monkeypatch, tmp_path):
    """A container must never silently use a file baked into the image."""
    path = tmp_path / "credentials.bin"
    if creds.available():
        creds.store("stale@example.com", "old", path)
    monkeypatch.setenv(creds.ENV_USERNAME, "fresh@example.com")
    monkeypatch.setenv(creds.ENV_PASSWORD, "new")
    assert creds.load(path).username == "fresh@example.com"


def test_half_set_environment_is_ignored(monkeypatch, tmp_path):
    """An email with no password must not be mistaken for credentials."""
    monkeypatch.setenv(creds.ENV_USERNAME, "team@example.com")
    monkeypatch.delenv(creds.ENV_PASSWORD, raising=False)
    assert creds.from_environment() is None
    with pytest.raises(creds.CredentialError):
        creds.load(tmp_path / "nothing.bin")


def test_environment_error_names_both_variables(monkeypatch, tmp_path):
    monkeypatch.delenv(creds.ENV_USERNAME, raising=False)
    monkeypatch.delenv(creds.ENV_PASSWORD, raising=False)
    with pytest.raises(creds.CredentialError, match=creds.ENV_USERNAME):
        creds.load(tmp_path / "nothing.bin")


def test_environment_credentials_are_never_writable(monkeypatch):
    """The container is handed its secret and has nowhere to store one."""
    monkeypatch.setenv(creds.ENV_USERNAME, "team@example.com")
    monkeypatch.setenv(creds.ENV_PASSWORD, "s3cret")
    assert creds.exists()  # readable
    assert creds.available() == (creds._dpapi() is not None)  # storability unchanged


# ---- read-only mode ------------------------------------------------------


@pytest.mark.parametrize("value, expected", [
    ("1", True), ("true", True), ("TRUE", True), ("yes", True), ("on", True),
    ("0", False), ("false", False), ("", False), ("no", False),
])
def test_read_only_flag_parsing(monkeypatch, value, expected):
    monkeypatch.setenv("CTC_READ_ONLY", value)
    from ctc_bot import server
    reloaded = importlib.reload(server)
    assert reloaded.READ_ONLY is expected


def test_read_only_covers_every_routed_write(monkeypatch):
    """The router and read-only share one list, so they cannot diverge.

    Kept as a test because the failure it guards against is silent: a new
    endpoint routed but not covered would be writable with read-only on.
    """
    monkeypatch.setenv("CTC_READ_ONLY", "1")
    from ctc_bot import server
    reloaded = importlib.reload(server)

    source = __import__("pathlib").Path(reloaded.__file__).read_text(encoding="utf-8")
    assert "if self.path not in _WRITE_PATHS:" in source
    assert "if READ_ONLY and self.path in _WRITE_PATHS:" in source

    # And the list is the real set of mutating endpoints.
    assert set(reloaded._WRITE_PATHS) == {
        "/api/claim", "/api/adopt", "/api/disown",
        "/api/edit-time", "/api/reset-time", "/api/add-result", "/api/remove-result",
    }


def test_host_and_port_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("CTC_HOST", "0.0.0.0")
    monkeypatch.setenv("PORT", "9999")
    from ctc_bot import server
    reloaded = importlib.reload(server)
    assert reloaded.HOST == "0.0.0.0"
    assert reloaded.DEFAULT_PORT == 9999


# ---- the weekly refresh --------------------------------------------------


@pytest.mark.parametrize("now, expected", [
    # Tuesday evening, after the time trial -> Wednesday morning
    (datetime(2026, 8, 18, 19, 30), datetime(2026, 8, 19, 7, 0)),
    # Wednesday before the slot -> same morning
    (datetime(2026, 8, 19, 6, 59), datetime(2026, 8, 19, 7, 0)),
    # Exactly on the slot -> the next one, never the same one twice
    (datetime(2026, 8, 19, 7, 0), datetime(2026, 8, 21, 7, 0)),
    # Thursday evening, after the aquathon -> Friday morning
    (datetime(2026, 8, 20, 20, 0), datetime(2026, 8, 21, 7, 0)),
    # Weekend -> the following Wednesday
    (datetime(2026, 8, 22, 12, 0), datetime(2026, 8, 26, 7, 0)),
])
def test_next_run(now, expected):
    assert sched.next_run(now) == expected


def test_slots_follow_the_race_days():
    """Wednesday and Friday, the mornings after Tuesday TT and Thursday aquathon."""
    assert sched.DEFAULT_DAYS == (sched.WEDNESDAY, sched.FRIDAY)
    assert datetime(2026, 8, 19).weekday() == sched.WEDNESDAY
    assert datetime(2026, 8, 21).weekday() == sched.FRIDAY


def test_a_failed_refresh_does_not_stop_the_schedule(monkeypatch):
    """One network blip must not silently end every future refresh."""
    scheduler = sched.Scheduler()
    monkeypatch.setattr(sched, "refresh", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    scheduler._run_once()
    assert scheduler.last_result == "failed - see log"
    assert scheduler.last_run is not None


# ---- the dashboard cache -------------------------------------------------


def test_cache_rebuilds_only_when_the_data_changes(tmp_path, monkeypatch):
    from ctc_bot import dashboard

    calls = {"n": 0}
    real_build = dashboard.build

    def counting_build(*, out_path=None):
        calls["n"] += 1
        return real_build(out_path=out_path)

    monkeypatch.setattr(dashboard, "build", counting_build)
    monkeypatch.setitem(dashboard._cache, "fingerprint", None)

    target = tmp_path / "dashboard.html"
    dashboard.build_if_stale(out_path=target)
    dashboard.build_if_stale(out_path=target)
    dashboard.build_if_stale(out_path=target)
    assert calls["n"] == 1

    from ctc_bot import config
    config.CONFIG_PATH.touch()
    dashboard.build_if_stale(out_path=target)
    assert calls["n"] == 2


# ---- HEAD ----------------------------------------------------------------


def test_head_is_answered_like_get():
    """Uptime monitors probe with HEAD; the default handler 501s on it."""
    from ctc_bot import server

    assert hasattr(server._Handler, "do_HEAD")
    source = __import__("pathlib").Path(server.__file__).read_text(encoding="utf-8")
    # It must reuse do_GET rather than duplicating the routing table.
    assert "self.do_GET()" in source
    # And it must suppress the body, not just the write.
    assert "if not self._head_only:" in source
