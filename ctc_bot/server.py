"""Local web server for the dashboard and the claim form.

Serves the generated page and one small API the page calls to record a claim.
Claims have to write to ``data/identity.json``, which a ``file://`` page cannot
do - that is the only reason a server exists.

**Authentication lives here**, in :mod:`ctc_bot.auth` - not in Traefik. It began
as a ``basicauth`` middleware, which worked but could only ever show the
browser's own credential dialog. Moving it into the app buys a real login page,
and moves the responsibility with it: nothing upstream checks a password now.

With no ``CTC_SITE_PASSWORD`` set, authentication is off and binding to
``127.0.0.1`` is the whole protection - that is how the local install runs.

``CTC_READ_ONLY`` refuses every mutating endpoint regardless. It exists because
a middleware that fails to attach does so silently, and this turns that from
"anyone can rewrite the club's history" into "nobody can change anything".
"""

from __future__ import annotations

import json
import os
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote_plus, urlparse

from . import admin_page
from . import auth
from . import dashboard
from . import identity as idn
from . import login_page
from . import merge as mrg
from . import scheduler as sched
from . import overrides as ovr
from . import store

HOST = os.environ.get("CTC_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.environ.get("PORT", "8777"))

# Refuse every mutating endpoint. The deployed instance sits behind Traefik
# basicauth, but a middleware that silently fails to attach is the realistic
# way that protection disappears - and it disappears without any error. This
# makes that failure mode fail closed instead of publishing a write API to the
# internet. Ships on; turned off once the password prompt is confirmed working.
READ_ONLY = os.environ.get("CTC_READ_ONLY", "").strip().lower() in {"1", "true", "yes", "on"}

# The host the name-tidying tools answer on, e.g. tri-admin.jamescarron.cloud.
# Set it and those tools exist *only* there: the club's page carries no trace of
# them, and their endpoints 404 on it. Leave it unset - the local install - and
# they live at /admin, because on 127.0.0.1 there is nobody to separate from.
#
# Worth being honest about what this is. Traefik takes a certificate per router,
# so the subdomain shows up in public Certificate Transparency logs as soon as
# it serves; it is not a secret. It stops members wandering into a tool that
# rewrites who owns which results. The password is what stops anyone else.
ADMIN_HOST = os.environ.get("CTC_ADMIN_HOST", "").strip().lower()

_ADMIN_PATHS = ("/api/merge-suggestions", "/api/merge", "/api/dismiss-merge")

_WRITE_PATHS = (
    "/api/claim", "/api/adopt", "/api/disown",
    "/api/edit-time", "/api/reset-time", "/api/add-result", "/api/remove-result",
    "/api/opt-out",
    "/api/merge", "/api/dismiss-merge",
)


class _Handler(BaseHTTPRequestHandler):
    """Serves the dashboard and handles claims."""

    server_version = "CTC_bot"

    def log_message(self, fmt, *args):  # quieter than the default access log
        if "api/" in (self.path or ""):
            print(f"  {self.command} {self.path}")

    # ---- helpers ----

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        # A HEAD response carries the headers of the GET it mirrors, including
        # Content-Length, but never the body.
        if not self._head_only:
            self.wfile.write(body)

    def _json(self, status: int, payload: dict) -> None:
        self._send(status, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8")

    def _redirect(self, location: str, *, cookie: str | None = None) -> None:
        self.send_response(303)
        self.send_header("Location", location)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _cookie(self, name: str) -> str | None:
        raw = self.headers.get("Cookie") or ""
        for part in raw.split(";"):
            key, _, value = part.strip().partition("=")
            if key == name:
                return value
        return None

    @property
    def _client(self) -> str:
        """Who is knocking, for lockout purposes.

        Behind Cloudflare and Traefik every request arrives from the proxy, so
        the socket address would lump the whole internet into one bucket and
        lock everybody out together. warp-auto rewrites X-Forwarded-For to the
        real visitor (ARCHITECTURE.md §7), which is what the rate limiter uses
        too.
        """
        forwarded = (self.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
        return forwarded or self.client_address[0]

    @property
    def _host(self) -> str:
        """The name the visitor actually typed.

        Behind Traefik the socket knows nothing useful, but the Host header is
        forwarded intact. Port stripped: a local run reaches this as
        ``127.0.0.1:8777``.
        """
        return (self.headers.get("Host") or "").split(":")[0].strip().lower()

    def _is_admin_host(self) -> bool:
        """Whether this request may see the name-tidying tools.

        With no admin host configured every request qualifies - that is the
        local install, bound to loopback. With one configured, only it does.
        """
        return not ADMIN_HOST or self._host == ADMIN_HOST

    def _authenticated(self) -> bool:
        return not auth.is_enabled() or auth.verify_token(self._cookie(auth.COOKIE_NAME))

    def _session_cookie(self, value: str, *, max_age: int) -> str:
        # Secure is safe to set unconditionally: the deployed site is HTTPS-only,
        # and a local run over http has no password set, so no cookie is issued.
        return (
            f"{auth.COOKIE_NAME}={value}; Path=/; HttpOnly; SameSite=Lax; "
            f"Secure; Max-Age={max_age}"
        )

    # ---- routes ----

    def do_GET(self) -> None:  # noqa: N802 - required name
        parsed = urlparse(self.path)

        # Health is deliberately open: Docker's healthcheck and Uptime Kuma
        # both probe it, and neither can hold a session. It reveals nothing.
        if parsed.path == "/api/health":
            self._json(200, {"ok": True, "readOnly": READ_ONLY})
            return

        if parsed.path == "/login":
            if self._authenticated():
                self._redirect("/")
                return
            nxt = (parse_qs(parsed.query).get("next") or ["/"])[0]
            self._send(200, login_page.render(next_path=nxt).encode("utf-8"),
                       "text/html; charset=utf-8")
            return

        if parsed.path == "/logout":
            self._redirect("/login", cookie=self._session_cookie("", max_age=0))
            return

        if not self._authenticated():
            self._deny(parsed.path)
            return

        # Everything below the admin tools is refused off the admin host, so
        # the club's site cannot reach them even if somebody knows the paths.
        if parsed.path in _ADMIN_PATHS and not self._is_admin_host():
            self._json(404, {"ok": False, "message": "Not found"})
            return

        if parsed.path in ("/", "/index.html", "/dashboard.html"):
            # On the admin host, the root *is* the admin page - there is no
            # link to find, and no club dashboard sitting behind it.
            if ADMIN_HOST and self._host == ADMIN_HOST:
                self._send(200, admin_page.render().encode("utf-8"),
                           "text/html; charset=utf-8")
                return
            path = dashboard.build_if_stale()
            self._send(200, path.read_bytes(), "text/html; charset=utf-8")
        elif parsed.path == "/admin":
            if ADMIN_HOST:
                # Deployed: this belongs on the admin host's root, not here.
                self._json(404, {"ok": False, "message": "Not found"})
                return
            self._send(200, admin_page.render().encode("utf-8"),
                       "text/html; charset=utf-8")
        elif parsed.path == "/api/rows":
            query = (parse_qs(parsed.query).get("q") or [""])[0]
            self._json(200, {"ok": True, "rows": search_rows(query)})
        elif parsed.path == "/api/merge-suggestions":
            self._json(200, {"ok": True, "suggestions": merge_suggestions()})
        else:
            self._json(404, {"ok": False, "message": "Not found"})

    def _deny(self, path: str) -> None:
        """Send an unauthenticated caller somewhere useful.

        A browser gets the login page; an API call gets JSON, because a page of
        HTML in response to fetch() is just a confusing parse error.
        """
        if path.startswith("/api/"):
            self._json(401, {"ok": False, "message": "Not signed in."})
        else:
            self._redirect(f"/login?next={quote(path, safe='/')}")

    def do_HEAD(self) -> None:  # noqa: N802 - required name
        """Answer HEAD as GET-without-a-body.

        BaseHTTPRequestHandler returns 501 otherwise, and uptime monitors
        commonly probe with HEAD - which would report the site as down while it
        was serving perfectly well.
        """
        self._head_only = True
        try:
            self.do_GET()
        finally:
            self._head_only = False

    _head_only = False

    def _read_body(self) -> bytes:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return b""
        return self.rfile.read(length) if length > 0 else b""

    def _handle_login(self) -> None:
        client = self._client
        locked = auth.attempts.locked_for(client)
        if locked:
            minutes = max(1, round(locked / 60))
            self._send(
                429,
                login_page.render(
                    error=f"Too many attempts. Try again in about {minutes} minute"
                          f"{'' if minutes == 1 else 's'}."
                ).encode("utf-8"),
                "text/html; charset=utf-8",
            )
            return

        form = parse_qs(self._read_body().decode("utf-8", "replace"))
        supplied = (form.get("password") or [""])[0]
        nxt = unquote_plus((form.get("next") or ["/"])[0]) or "/"
        # Only ever redirect within this site - an attacker-supplied absolute
        # URL here would turn the login page into an open redirect.
        if not nxt.startswith("/") or nxt.startswith("//"):
            nxt = "/"

        if auth.check_password(supplied):
            auth.attempts.clear(client)
            self._redirect(
                nxt,
                cookie=self._session_cookie(auth.make_token(), max_age=auth.SESSION_HOURS * 3600),
            )
            return

        auth.attempts.record_failure(client)
        self._send(
            401,
            login_page.render(error="That password was not recognised.", next_path=nxt).encode("utf-8"),
            "text/html; charset=utf-8",
        )

    def do_POST(self) -> None:  # noqa: N802 - required name
        if self.path == "/login":
            self._handle_login()
            return

        if not self._authenticated():
            self._deny(self.path)
            return

        if READ_ONLY and self.path in _WRITE_PATHS:
            self._json(
                403,
                {
                    "ok": False,
                    "message": "This dashboard is read-only. Changes are disabled.",
                },
            )
            return

        # One list, used both as the router's allowlist and as what read-only
        # refuses. Two lists would let a new endpoint be routed but not covered,
        # and nothing would report it.
        if self.path not in _WRITE_PATHS:
            self._json(404, {"ok": False, "message": "Not found"})
            return

        if self.path in _ADMIN_PATHS and not self._is_admin_host():
            self._json(404, {"ok": False, "message": "Not found"})
            return

        try:
            request = json.loads(self._read_body() or b"{}")
        except (ValueError, TypeError):
            self._json(400, {"ok": False, "message": "Could not read the request."})
            return

        athlete_id = (request.get("athleteId") or "").strip()
        event = (request.get("event") or "").strip()
        race_id = str(request.get("raceId") or "").strip()

        try:
            if self.path == "/api/claim":
                name = (request.get("name") or "").strip()
                if not name or not athlete_id:
                    self._json(
                        400, {"ok": False, "message": "A name and an athlete are both required."}
                    )
                    return
                message = claim_athlete(athlete_id, name)
            elif self.path == "/api/add-result":
                message = add_result(
                    athlete_id,
                    request.get("raceType") or "",
                    request.get("date") or "",
                    request.get("seconds"),
                    title=request.get("title") or "Added by hand",
                )
            elif self.path == "/api/remove-result":
                message = remove_result(str(request.get("additionId") or ""))
            elif self.path == "/api/opt-out":
                message = opt_out(athlete_id, request.get("name") or "")
            elif self.path == "/api/merge":
                message = apply_merge(
                    str(request.get("key") or ""), str(request.get("displayName") or "")
                )
            elif self.path == "/api/dismiss-merge":
                message = dismiss_merge(str(request.get("key") or ""))
            else:
                if not (athlete_id and event and race_id):
                    self._json(
                        400,
                        {"ok": False, "message": "An athlete, event and result are all required."},
                    )
                    return
                message = _row_action(self.path, request, athlete_id, event, race_id)
        except LookupError as exc:
            self._json(404, {"ok": False, "message": str(exc)})
            return
        except ValueError as exc:
            self._json(400, {"ok": False, "message": str(exc)})
            return

        self._json(200, {"ok": True, "message": message})


def _curated_events():
    from . import curation

    included, _ = curation.partition(store.load_all())
    return included


def _find_row(stored, race_id: str) -> dict | None:
    return next(
        (r for r in stored.event.results if str(r.get("RaceID")) == str(race_id)), None
    )


def _candidate(stored, row: dict) -> idn.Candidate:
    return idn.Candidate(
        event=stored.code,
        event_title=stored.title,
        date_text=stored.date_text,
        race_type=stored.race_type,
        race_id=str(row.get("RaceID")),
        name=row.get("Name", ""),
        bib=str(row.get("Bib", "")),
        seconds=None,
        position=None,
    )


# ---- merging duplicate spellings ----------------------------------------


# High enough that the club's 129 suggestions all render, low enough that a
# pathological dataset cannot build a page nobody can load. The tool has its own
# host and its own filters now; a cap that quietly hid 49 of the 53 weak
# suggestions - while the filter button still counted them - was worse than no
# cap at all.
SUGGESTION_LIMIT = 500


def merge_suggestions(limit: int = SUGGESTION_LIMIT) -> list[dict]:
    """Spellings that look like one person, for an admin to confirm.

    Read-only. Nothing here changes a single claim - see :mod:`ctc_bot.merge`
    for why the machine only ever proposes.
    """
    registry = idn.Registry.load()
    found = mrg.suggest(_curated_events(), registry)
    return [
        {
            "key": s.key,
            "name": s.display_name,
            "races": s.races,
            "confidence": s.confidence,
            "reasons": s.reasons,
            "raceTypes": s.race_types,
            "joinsClaimed": s.joins_claimed,
            "mergesInto": (
                registry.athletes[s.target_id].display_name
                if s.target_id and s.target_id in registry.athletes
                else None
            ),
            "variants": [
                {"name": v.name, "races": v.races, "owner": v.owner_name} for v in s.variants
            ],
        }
        for s in found[:limit]
    ]


def apply_merge(key: str, display_name: str = "") -> str:
    """Claim every spelling in one suggestion under a single athlete."""
    if not key:
        raise ValueError("Which suggestion?")
    registry = idn.Registry.load()
    events = _curated_events()
    suggestion = mrg.find(key, events, registry)
    if suggestion is None:
        # Either somebody already merged it, or the underlying rows moved. Both
        # mean the page is out of date rather than that anything went wrong.
        raise LookupError("That suggestion is no longer current. Refresh and look again.")

    moved = mrg.apply(suggestion, events, registry, display_name=display_name)
    registry.save()
    name = (display_name or suggestion.display_name).strip()
    return f"Joined {len(suggestion.variants)} spellings into {name!r} - {moved} results."


def dismiss_merge(key: str) -> str:
    """Record that somebody looked at a suggestion and said no."""
    if not key:
        raise ValueError("Which suggestion?")
    registry = idn.Registry.load()
    registry.dismissed_merges.add(key)
    registry.save()
    return "Suggestion dismissed; it will not be offered again."


def search_rows(query: str, limit: int = 60) -> list[dict]:
    """Result rows whose first name matches, with whoever currently owns each.

    Used by the dashboard to find a stray result and attach it to an athlete -
    the case where somebody raced under a spelling nobody would think to look
    for.
    """
    query = (query or "").strip()
    if len(query) < 2:
        return []

    registry = idn.Registry.load()
    events = _curated_events()
    rows = []
    for candidate in registry.search(query, events):
        owner = (
            registry.athletes[candidate.claimed_by].display_name
            if candidate.claimed_by
            else None
        )
        rows.append(
            {
                "event": candidate.event,
                "raceId": candidate.race_id,
                "name": candidate.name.strip(),
                "date": candidate.date_text,
                "title": candidate.event_title,
                "raceType": candidate.race_type,
                "time": candidate.time,
                "owner": owner,
                "ownerId": candidate.claimed_by,
            }
        )
    rows.sort(key=lambda r: r["date"] or "", reverse=True)
    return rows[:limit]


def _parse_seconds(value) -> float:
    """Read a time given either as seconds or as mm:ss(.s) / h:mm:ss(.s)."""
    if value is None or value == "":
        raise ValueError("A time is required.")
    text = str(value).strip()
    if ":" in text:
        parts = text.split(":")
        if len(parts) > 3:
            raise ValueError(f"Could not read the time {text!r}.")
        try:
            total = 0.0
            for part in parts:
                total = total * 60 + float(part)
        except ValueError:
            raise ValueError(f"Could not read the time {text!r}. Try 25:37.2") from None
        return total
    try:
        return float(text)
    except ValueError:
        raise ValueError(f"Could not read the time {text!r}. Try 25:37.2") from None


def _row_action(path: str, request: dict, athlete_id: str, event: str, race_id: str) -> str:
    if path == "/api/adopt":
        return adopt_row(athlete_id, event, race_id)
    if path == "/api/disown":
        return disown_row(athlete_id, event, race_id)
    if path == "/api/edit-time":
        return edit_time(event, race_id, request.get("time"))
    return reset_time(event, race_id)


def edit_time(event: str, race_id: str, value) -> str:
    """Correct one published time, keeping the original for reset."""
    seconds = _parse_seconds(value)

    stored = next((s for s in _curated_events() if s.code == event), None)
    if stored is None:
        raise LookupError("That event is not in the local store.")
    row = _find_row(stored, race_id)
    if row is None:
        raise LookupError("That result is not in the event.")

    try:
        published = float(row.get("TmResultSec"))
    except (TypeError, ValueError):
        published = 0.0

    corrections = ovr.Overrides.load()
    corrections.edit_time(event, race_id, seconds, published)
    corrections.save()
    return (
        f"Time set to {idn.format_time(seconds)} "
        f"(published: {idn.format_time(published)}). The original is kept, so this can be reset."
    )


def reset_time(event: str, race_id: str) -> str:
    """Drop a correction and restore the published time."""
    corrections = ovr.Overrides.load()
    removed = corrections.reset_time(event, race_id)
    if removed is None:
        raise LookupError("That result has not been corrected.")
    corrections.save()
    return f"Restored the published time, {idn.format_time(removed.original_seconds)}."


def add_result(athlete_id: str, race_type: str, when: str, value, *, title: str) -> str:
    """Record a race the timing system never captured."""
    if not athlete_id:
        raise ValueError("An athlete is required.")
    if race_type not in ("time_trial", "aquathon"):
        raise ValueError(f"Unknown race type {race_type!r}.")

    registry = idn.Registry.load()
    if athlete_id not in registry.athletes:
        raise LookupError(
            "Confirm who this athlete is first - a hand-added result has to "
            "belong to a confirmed person."
        )

    seconds = _parse_seconds(value)
    corrections = ovr.Overrides.load()
    addition = corrections.add_result(
        athlete_id, race_type, when, seconds, title=title
    )
    corrections.save()
    return (
        f"Added {idn.format_time(seconds)} on {addition.when}. It is marked as "
        "added by hand and can be removed again."
    )


def remove_result(addition_id: str) -> str:
    """Delete a hand-added result."""
    if not addition_id:
        raise ValueError("A result is required.")
    corrections = ovr.Overrides.load()
    removed = corrections.remove_result(addition_id)
    if removed is None:
        raise LookupError("No such hand-added result.")
    corrections.save()
    return f"Removed the hand-added result from {removed.when}."


def opt_out(athlete_id: str, display_name: str) -> str:
    """Remove someone from the site at their own request.

    Their results stay in the store and keep counting towards the field a race
    is measured against - they did race, and dropping them would quietly change
    everyone else's z-score and finishing position. They simply stop appearing
    as a person: no name, no profile, no standings entry.

    Reversible: the entry, including the name, is kept in identity.json so an
    admin can undo it.
    """
    if not athlete_id:
        raise ValueError("An athlete is required.")

    registry = idn.Registry.load()
    if registry.has_opted_out(athlete_id):
        return "Already removed from the site."

    name = display_name.strip() or registry.athletes.get(
        athlete_id, idn.Athlete(athlete_id, athlete_id)
    ).display_name
    registry.opt_out(athlete_id, name)
    registry.save()
    return (
        f"{name} removed from the site. The results stay in the club's records "
        "and still count towards each race's field, but no longer appear here."
    )


def adopt_row(athlete_id: str, event: str, race_id: str) -> str:
    """Attach one specific result to an already-confirmed athlete."""
    registry = idn.Registry.load()
    athlete = registry.athletes.get(athlete_id)
    if athlete is None:
        raise LookupError(
            "Confirm who this athlete is first - an individual result can only "
            "be added to a confirmed person."
        )

    stored = next((s for s in _curated_events() if s.code == event), None)
    if stored is None:
        raise LookupError("That event is not in the local store.")
    row = _find_row(stored, race_id)
    if row is None:
        raise LookupError("That result is not in the event.")

    name = (row.get("Name") or "").strip()
    registry.claim(athlete.display_name, [_candidate(stored, row)], athlete_id=athlete_id)
    registry.save()
    return f"Added {name!r} from {stored.title} to {athlete.display_name}."


def disown_row(athlete_id: str, event: str, race_id: str) -> str:
    """Detach one result from an athlete for good.

    Nothing is deleted - the row goes back to being grouped by the name printed
    on the entry list, exactly as it was before anyone claimed it.

    Releasing the claim is not enough on its own, and this is the bug that made
    the button look broken. A spelling learned from an athlete's other claims
    pulls every matching row back by *inference* the moment the claim goes, so
    saying "not mine" about a row entered as *James* - a spelling James Carron
    claims sixteen times elsewhere - undid itself before the page had finished
    reloading. The decision is recorded as well, and inference respects it.

    That also means this works on a row nobody claimed. An inferred row is the
    case most likely to be wrong, since nobody ever looked at it.
    """
    registry = idn.Registry.load()
    athlete = registry.athletes.get(athlete_id)
    if athlete is None:
        raise LookupError("No such athlete.")

    claim = next((c for c in athlete.claims if c.key == (event, str(race_id))), None)
    if claim is not None:
        original = claim.name.strip() or "(unnamed)"
        registry.release(athlete_id, event, race_id)
    else:
        # Inferred rather than claimed: the name on the entry list is whatever
        # the event recorded, so read it from there.
        stored = next((s for s in _curated_events() if s.code == event), None)
        row = _find_row(stored, race_id) if stored else None
        if row is None:
            raise LookupError("That result is not in the event.")
        # Only a row currently attributed to this athlete can be taken off
        # them. Anything else is somebody else's result, and recording that it
        # is "not theirs" would be a decision about a row they never had.
        using = registry.athletes_using(idn.normalise(row.get("Name") or ""))
        if [a.id for a in using] != [athlete_id]:
            raise LookupError("That result is not attributed to this athlete.")
        original = (row.get("Name") or "").strip() or "(unnamed)"

    registry.exclude(athlete_id, event, race_id)

    # An athlete with nothing left is no longer a confirmed identity; leaving an
    # empty shell behind would put a person with no races in the standings.
    if not athlete.claims:
        display = athlete.display_name
        del registry.athletes[athlete_id]
        registry.save()
        return (
            f"Released back to {original!r}. {display} had no other results, so "
            "that identity is gone too."
        )

    registry.save()
    return f"Released back to {original!r}, as printed on the entry list."


def claim_athlete(athlete_id: str, display_name: str) -> str:
    """Record every row currently attributed to ``athlete_id`` under ``display_name``.

    The dashboard groups unclaimed athletes provisionally by exact name; this
    turns one of those groups into a real claim, which then teaches the spelling
    for future events.
    """
    registry = idn.Registry.load()
    included = _curated_events()
    resolutions = idn.resolve(included, registry)

    by_code = {stored.code: stored for stored in included}
    candidates: list[idn.Candidate] = []

    for (code, race_id), resolution in resolutions.items():
        if resolution.athlete_id != athlete_id:
            continue
        stored = by_code.get(code)
        if stored is None:
            continue
        row = next(
            (r for r in stored.event.results if str(r.get("RaceID")) == race_id), None
        )
        if row is None:
            continue
        candidates.append(
            idn.Candidate(
                event=code,
                event_title=stored.title,
                date_text=stored.date_text,
                race_type=stored.race_type,
                race_id=race_id,
                name=row.get("Name", ""),
                bib=str(row.get("Bib", "")),
                seconds=None,
                position=None,
            )
        )

    if not candidates:
        raise LookupError("No results found for that athlete - the page may be out of date.")

    athlete = registry.claim(display_name, candidates)
    registry.save()
    variants = ", ".join(sorted(athlete.name_variants))
    return f"{len(candidates)} result(s) recorded for {athlete.display_name} (races as: {variants})"


def serve(port: int = DEFAULT_PORT, *, open_browser: bool = True, schedule: bool = False) -> None:
    """Run the dashboard server until interrupted."""
    # Refuse to serve the internet with no password. Authentication being off is
    # correct on 127.0.0.1 and catastrophic on 0.0.0.0 - a deploy that lost the
    # secret would otherwise publish the club's whole history, and every write
    # endpoint, with nothing to notice it had happened.
    if HOST not in ("127.0.0.1", "localhost", "::1") and not auth.is_enabled():
        raise SystemExit(
            f"Refusing to bind {HOST} with no {auth.ENV_PASSWORD} set.\n"
            "That would serve the dashboard, and every write endpoint, to anyone.\n"
            "Set the password, or bind to 127.0.0.1 for local use."
        )

    dashboard.build()

    refresher = None
    if schedule:
        refresher = sched.Scheduler()
        refresher.start()

    httpd = ThreadingHTTPServer((HOST, port), _Handler)
    url = f"http://{HOST}:{port}/"

    print("CTC_bot dashboard")
    print("=" * 52)
    print(f"  {url}")
    if READ_ONLY:
        print("  READ-ONLY: claims, edits and additions are refused.")
    print("  The page rebuilds on every load, so refresh after claiming.")
    print("  Press Ctrl+C to stop.")
    print("=" * 52)

    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        httpd.server_close()


def find_free_port(preferred: int = DEFAULT_PORT, attempts: int = 20) -> int:
    """First free port at or after ``preferred``."""
    import socket

    for offset in range(attempts):
        candidate = preferred + offset
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            if sock.connect_ex((HOST, candidate)) != 0:
                return candidate
    raise OSError(f"No free port found near {preferred}")


def out_path() -> Path:
    return dashboard.OUT_PATH
