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


# ---- race-night polling and the morning sweep ----------------------------


@pytest.mark.parametrize("when, expected", [
    (datetime(2026, 8, 18, 18, 59), False),  # Tuesday, before the window
    (datetime(2026, 8, 18, 19, 0), True),    # Tuesday, window opens
    (datetime(2026, 8, 18, 20, 30), True),   # Tuesday, mid race
    (datetime(2026, 8, 18, 22, 59), True),   # Tuesday, window closing
    (datetime(2026, 8, 18, 23, 0), False),   # Tuesday, window shut
    (datetime(2026, 8, 20, 20, 0), True),    # Thursday aquathon
    (datetime(2026, 8, 19, 20, 0), False),   # Wednesday is not a race night
    (datetime(2026, 8, 22, 20, 0), False),   # Saturday
])
def test_race_night_window(when, expected):
    assert sched.is_race_night(when) is expected


@pytest.mark.parametrize("now, expected", [
    # Before Tuesday's window -> the window, not the morning after
    (datetime(2026, 8, 18, 18, 0), datetime(2026, 8, 18, 19, 0)),
    # Inside it -> the sweep, since polling handles the rest of the evening
    (datetime(2026, 8, 18, 20, 0), datetime(2026, 8, 19, 7, 0)),
    # After Wednesday's sweep -> Thursday's window
    (datetime(2026, 8, 19, 8, 0), datetime(2026, 8, 20, 19, 0)),
    # Weekend -> the following Tuesday evening
    (datetime(2026, 8, 22, 12, 0), datetime(2026, 8, 25, 19, 0)),
])
def test_next_wakeup(now, expected):
    assert sched.next_wakeup(now) == expected


def test_race_days_and_sweep_days_line_up():
    """Sweep the morning after each race night, not on arbitrary days."""
    assert sched.RACE_DAYS == (sched.TUESDAY, sched.THURSDAY)
    assert sched.SWEEP_DAYS == (sched.WEDNESDAY, sched.FRIDAY)
    assert datetime(2026, 8, 18).weekday() == sched.TUESDAY
    assert datetime(2026, 8, 20).weekday() == sched.THURSDAY


def test_polling_stops_once_results_are_in(monkeypatch):
    """The whole point: stop hammering the console once tonight is settled."""
    scheduler = sched.Scheduler()
    calls = {"n": 0}

    def fake_poll(today=None):
        calls["n"] += 1
        return True, "1 event(s) with 15 result(s)"

    monkeypatch.setattr(sched, "poll_tonight", fake_poll)
    race_night = datetime(2026, 8, 18, 20, 0)

    scheduler._poll(race_night)
    assert scheduler.settled_on == race_night.date()
    # The loop's guard is settled_on, so a second evening pass is skipped.
    assert sched.is_race_night(race_night)
    assert scheduler.settled_on == race_night.date()
    assert calls["n"] == 1


def test_polling_continues_while_no_results_yet(monkeypatch):
    """An event created before the race must not end the night's checking."""
    scheduler = sched.Scheduler()
    monkeypatch.setattr(
        sched, "poll_tonight",
        lambda today=None: (False, "1 event(s) listed, none timed yet"),
    )
    scheduler._poll(datetime(2026, 8, 18, 19, 30))
    assert scheduler.settled_on is None


def test_a_failed_poll_does_not_end_the_night(monkeypatch):
    """One network blip must not stop the remaining checks."""
    scheduler = sched.Scheduler()
    monkeypatch.setattr(
        sched, "poll_tonight",
        lambda today=None: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    scheduler._poll(datetime(2026, 8, 18, 20, 0))
    assert scheduler.last_result == "poll failed - see log"
    assert scheduler.settled_on is None  # so it tries again


def test_a_failed_sweep_does_not_stop_the_schedule(monkeypatch):
    scheduler = sched.Scheduler()
    monkeypatch.setattr(
        sched.Scheduler, "sweep_once",
        staticmethod(lambda: (_ for _ in ()).throw(RuntimeError("boom"))),
    )
    scheduler._sweep()
    assert scheduler.last_result == "sweep failed - see log"
    assert scheduler.last_run is not None


def test_poll_interval_is_five_minutes_by_default():
    assert sched.POLL_MINUTES == 5


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


# ---- the password-setting script -----------------------------------------


def test_password_script_recreates_rather_than_restarts():
    """docker reads env_file at container CREATION only.

    `docker restart` reuses the environment baked in at that moment, so a newly
    stored password never reaches the process - it silently keeps serving the
    old one, and the symptom is "the correct password is rejected". This was a
    real bug, found after the first live run.
    """
    from pathlib import Path

    script = Path(__file__).parent.parent / "scripts" / "set_server_password.ps1"
    body = script.read_text(encoding="utf-8")

    assert "--force-recreate" in body
    assert "docker restart tri-app-1" not in body


def test_password_script_verifies_the_container_received_it():
    """Storing the secret is not the same as the process seeing it."""
    from pathlib import Path

    body = (
        Path(__file__).parent.parent / "scripts" / "set_server_password.ps1"
    ).read_text(encoding="utf-8")

    # round-trip through encryption
    assert "password did not survive encryption" in body
    # and actually present in the running container's environment
    assert "container sees" in body
    # only then, whether RaceClocker accepts it
    assert "session.check()" in body


def test_password_script_never_puts_the_secret_in_argv():
    """On the server, argv is readable from ps by every local user."""
    from pathlib import Path

    body = (
        Path(__file__).parent.parent / "scripts" / "set_server_password.ps1"
    ).read_text(encoding="utf-8")

    assert 'ENVIRON["PW"]' in body      # not awk -v
    assert "IFS= read -r PW" in body    # arrives on stdin
    assert "awk -v pw=" not in body
