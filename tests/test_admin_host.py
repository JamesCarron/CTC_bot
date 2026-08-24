"""Tests for keeping the name-tidying tools off the club's site.

The point is not that the admin hostname is secret - Traefik takes a
certificate per router, so it is published to Certificate Transparency logs the
moment it serves. The point is that the club's page carries no trace of the
tools and cannot reach their endpoints, so hiding them is a property of the
server rather than of the markup.
"""

import importlib

import pytest

from ctc_bot import admin_page, dashboard, server


def recorder(module):
    """A subclass of the real handler, so the routing under test is the real one.

    ``BaseHTTPRequestHandler.__init__`` serves a request off a socket, so it is
    bypassed; everything the routes actually call is either inherited or
    captured here.
    """

    class _Recorder(module._Handler):
        def __init__(self, path, host):
            self.path = path
            self.headers = {"Host": host}
            self.status = None
            self.sent = None

        def _json(self, status, payload):
            self.status, self.sent = status, payload

        def _send(self, status, body, content_type):
            self.status, self.sent = status, body

        def _redirect(self, *a, **k):
            self.status = 303

        def _authenticated(self):
            return True

        def get(self):
            module._Handler.do_GET(self)
            return self

        def post(self, body=b"{}"):
            self.rfile = _Body(body)
            self.headers = {**self.headers, "Content-Length": str(len(body))}
            module._Handler.do_POST(self)
            return self

    return _Recorder


class _Body:
    def __init__(self, data):
        self._data = data

    def read(self, _n):
        return self._data


@pytest.fixture
def deployed(monkeypatch, tmp_path):
    """The server as it runs behind Traefik, with an admin host configured."""
    monkeypatch.setenv("CTC_ADMIN_HOST", "tri-admin.example.test")
    monkeypatch.delenv("CTC_READ_ONLY", raising=False)
    reloaded = importlib.reload(server)
    # The club page is expensive to build and irrelevant here.
    page = tmp_path / "dashboard.html"
    page.write_text("<html>club</html>", encoding="utf-8")
    monkeypatch.setattr(reloaded.dashboard, "build_if_stale", lambda **k: page)
    monkeypatch.setattr(reloaded, "merge_suggestions", lambda *a, **k: [])
    monkeypatch.setattr(reloaded, "apply_merge", lambda *a, **k: "merged")
    monkeypatch.setattr(reloaded, "dismiss_merge", lambda *a, **k: "dismissed")
    yield reloaded
    importlib.reload(server)


@pytest.fixture
def local(monkeypatch):
    """The local install: loopback, no admin host, nobody to hide from."""
    monkeypatch.delenv("CTC_ADMIN_HOST", raising=False)
    monkeypatch.delenv("CTC_READ_ONLY", raising=False)
    reloaded = importlib.reload(server)
    monkeypatch.setattr(reloaded, "merge_suggestions", lambda *a, **k: [])
    yield reloaded
    importlib.reload(server)


CLUB = "tri.example.test"
ADMIN = "tri-admin.example.test"


# ---- the club host -------------------------------------------------------


def test_the_club_host_serves_the_dashboard(deployed):
    answer = recorder(deployed)("/", CLUB).get()
    assert answer.status == 200
    assert b"club" in answer.sent


def test_the_club_host_cannot_read_suggestions(deployed):
    answer = recorder(deployed)("/api/merge-suggestions", CLUB).get()
    assert answer.status == 404


@pytest.mark.parametrize("path", ["/api/merge", "/api/dismiss-merge"])
def test_the_club_host_cannot_merge(deployed, path):
    """Knowing the endpoint must not be enough. Hiding a button in the markup
    would leave exactly this open."""
    answer = recorder(deployed)(path, CLUB).post(b'{"key": "a|b"}')
    assert answer.status == 404


def test_the_admin_path_is_not_served_on_the_club_host(deployed):
    assert recorder(deployed)("/admin", CLUB).get().status == 404


def test_the_club_page_never_mentions_the_tools():
    """Not a hidden section and not a dormant fetch - nothing in view-source.

    The word "admin" is left alone: the opt-out copy tells people to ask one,
    which is about a person rather than a page.
    """
    page = dashboard._TEMPLATE.lower()
    for trace in ("merge", "suggestion", "/admin", "tri-admin"):
        assert trace not in page, f"the club page still mentions {trace!r}"


# ---- the admin host ------------------------------------------------------


def test_the_admin_host_serves_the_tools_at_its_root(deployed):
    answer = recorder(deployed)("/", ADMIN).get()
    assert answer.status == 200
    assert b"Tidying names" in answer.sent
    assert b"club" not in answer.sent


def test_the_admin_host_may_read_suggestions(deployed):
    answer = recorder(deployed)("/api/merge-suggestions", ADMIN).get()
    assert answer.status == 200
    assert answer.sent["ok"] is True


def test_the_admin_host_may_merge(deployed):
    answer = recorder(deployed)("/api/merge", ADMIN).post(b'{"key": "a|b"}')
    assert answer.status == 200


def test_a_port_on_the_host_header_does_not_matter(deployed):
    """Reaching the container directly puts a port in the header."""
    answer = recorder(deployed)("/api/merge-suggestions", f"{ADMIN}:8777").get()
    assert answer.status == 200


def test_the_admin_page_asks_not_to_be_indexed():
    assert 'name="robots"' in admin_page.render()
    assert "noindex" in admin_page.render()


# ---- the local install ---------------------------------------------------


def test_locally_the_tools_live_under_slash_admin(local):
    answer = recorder(local)("/admin", "127.0.0.1").get()
    assert answer.status == 200
    assert b"Tidying names" in answer.sent


def test_locally_the_endpoints_answer_anywhere(local):
    """Bound to loopback with no password, there is nobody to separate from."""
    answer = recorder(local)("/api/merge-suggestions", "127.0.0.1").get()
    assert answer.status == 200


def test_read_only_still_covers_the_admin_writes(monkeypatch):
    """CTC_READ_ONLY is the backstop for every write, these included."""
    monkeypatch.setenv("CTC_READ_ONLY", "1")
    monkeypatch.setenv("CTC_ADMIN_HOST", ADMIN)
    reloaded = importlib.reload(server)
    try:
        answer = recorder(reloaded)("/api/merge", ADMIN).post(b'{"key": "a|b"}')
        assert answer.status == 403
    finally:
        monkeypatch.undo()
        importlib.reload(server)
