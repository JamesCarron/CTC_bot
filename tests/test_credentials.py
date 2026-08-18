"""Tests for encrypted credential storage and the login session.

No real credentials and no network access: the secret used here is a dummy, and
the login flow is driven through a stub session.
"""

import json

import pytest
import requests

from ctc_bot import credentials as creds
from ctc_bot import session as sess

DUMMY_USER = "dummy@example.com"
DUMMY_PASS = "not-a-real-password"

needs_dpapi = pytest.mark.skipif(
    not creds.available(), reason="no secure credential backend on this machine"
)


@pytest.fixture
def cred_path(tmp_path):
    return tmp_path / "nested" / "credentials.bin"


# ---- storage -------------------------------------------------------------


@needs_dpapi
def test_roundtrip(cred_path):
    creds.store(DUMMY_USER, DUMMY_PASS, cred_path)
    loaded = creds.load(cred_path)
    assert loaded.username == DUMMY_USER
    assert loaded.password == DUMMY_PASS


@needs_dpapi
def test_secret_is_not_readable_on_disk(cred_path):
    """The whole point: nothing recoverable by reading the file."""
    creds.store(DUMMY_USER, DUMMY_PASS, cred_path)
    raw = cred_path.read_bytes()
    assert DUMMY_PASS.encode() not in raw
    assert DUMMY_USER.encode() not in raw
    with pytest.raises(UnicodeDecodeError):
        json.loads(raw.decode("utf-8"))


@needs_dpapi
def test_repr_never_leaks_the_password(cred_path):
    """Guards against a traceback or log line exposing the secret."""
    creds.store(DUMMY_USER, DUMMY_PASS, cred_path)
    text = repr(creds.load(cred_path))
    assert DUMMY_PASS not in text
    assert "<hidden>" in text


@needs_dpapi
def test_corrupted_file_is_rejected(cred_path):
    creds.store(DUMMY_USER, DUMMY_PASS, cred_path)
    blob = cred_path.read_bytes()
    cred_path.write_bytes(blob[:-8] + b"\x00" * 8)
    with pytest.raises(creds.CredentialError):
        creds.load(cred_path)


@needs_dpapi
def test_parent_directories_are_created(cred_path):
    assert not cred_path.parent.exists()
    creds.store(DUMMY_USER, DUMMY_PASS, cred_path)
    assert cred_path.exists()


def test_missing_file_gives_actionable_error(cred_path):
    with pytest.raises(creds.CredentialError, match="set_credentials"):
        creds.load(cred_path)


@needs_dpapi
def test_exists_and_delete(cred_path):
    assert not creds.exists(cred_path)
    creds.store(DUMMY_USER, DUMMY_PASS, cred_path)
    assert creds.exists(cred_path)
    assert creds.delete(cred_path) is True
    assert not creds.exists(cred_path)
    assert creds.delete(cred_path) is False


def test_default_path_is_outside_the_repository():
    """A secret must never be able to land inside the git working tree."""
    from pathlib import Path

    repo = Path(__file__).resolve().parent.parent
    assert repo not in creds.default_path().resolve().parents


def test_env_var_overrides_default_path(tmp_path, monkeypatch):
    target = tmp_path / "custom.bin"
    monkeypatch.setenv("CTC_BOT_CREDENTIALS", str(target))
    assert creds.default_path() == target


# ---- login ---------------------------------------------------------------


class StubResponse:
    def __init__(self, text, url="https://raceclocker.com/My_Events.php"):
        self.text = text
        self.url = url

    def raise_for_status(self):
        return None


class StubSession:
    """Stands in for requests.Session, recording what was posted."""

    def __init__(self, response_text, *, get_text=None):
        self.response_text = response_text
        self.get_text = get_text if get_text is not None else response_text
        self.headers = {}
        self.posted = None

    def post(self, url, data=None, **kwargs):
        self.posted = (url, data)
        return StubResponse(self.response_text)

    def get(self, url, **kwargs):
        return StubResponse(self.get_text, url=url)


LOGIN_PAGE = '<form action="Login.php"><input name="fld_password" type="password"/></form>'
ADMIN_PAGE = '<div id="EventList">My Events</div>'


def test_login_posts_the_expected_form_fields():
    stub = StubSession(ADMIN_PAGE)
    sess.login(DUMMY_USER, DUMMY_PASS, session=stub)
    url, data = stub.posted
    assert url == sess.LOGIN_URL
    assert data == {"fld_email": DUMMY_USER, "fld_password": DUMMY_PASS}


def test_login_rejected_when_form_comes_back():
    """Being handed the login form again means the credentials were refused."""
    stub = StubSession(LOGIN_PAGE)
    with pytest.raises(sess.LoginError, match="Login rejected"):
        sess.login(DUMMY_USER, DUMMY_PASS, session=stub)


def test_is_logged_out_detects_the_login_form():
    assert sess.is_logged_out(LOGIN_PAGE)
    assert not sess.is_logged_out(ADMIN_PAGE)


def test_fetch_raises_when_session_expired():
    stub = StubSession(ADMIN_PAGE, get_text=LOGIN_PAGE)
    authed = sess.login(DUMMY_USER, DUMMY_PASS, session=stub)
    with pytest.raises(sess.LoginError, match="expired"):
        sess.fetch(sess.EVENTS_URL, session=authed)


def test_fetch_returns_authenticated_html():
    stub = StubSession(ADMIN_PAGE)
    authed = sess.login(DUMMY_USER, DUMMY_PASS, session=stub)
    assert sess.fetch_event_list(session=authed) == ADMIN_PAGE


def test_network_failure_is_reported_clearly(monkeypatch):
    class Broken(StubSession):
        def post(self, url, data=None, **kwargs):
            raise requests.ConnectionError("boom")

    with pytest.raises(sess.LoginError, match="Could not reach RaceClocker"):
        sess.login(DUMMY_USER, DUMMY_PASS, session=Broken(ADMIN_PAGE))
