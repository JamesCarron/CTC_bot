"""Local web server for the dashboard and the claim form.

Serves the generated page and one small API the page calls to record a claim.
Claims have to write to ``data/identity.json``, which a ``file://`` page cannot
do - that is the only reason a server exists.

**This server has no authentication of its own.** Locally it binds to
``127.0.0.1`` and that is the whole protection. Deployed, it binds to all
interfaces (``CTC_HOST=0.0.0.0``) and sits behind Traefik, where a ``basicauth``
middleware is what keeps the write API from being world-writable.

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

from . import dashboard
from . import identity as idn
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

_WRITE_PATHS = (
    "/api/claim", "/api/adopt", "/api/disown",
    "/api/edit-time", "/api/reset-time", "/api/add-result", "/api/remove-result",
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
        self.wfile.write(body)

    def _json(self, status: int, payload: dict) -> None:
        self._send(status, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8")

    # ---- routes ----

    def do_GET(self) -> None:  # noqa: N802 - required name
        from urllib.parse import parse_qs, urlparse

        parsed = urlparse(self.path)
        if parsed.path in ("/", "/index.html", "/dashboard.html"):
            path = dashboard.build_if_stale()
            self._send(200, path.read_bytes(), "text/html; charset=utf-8")
        elif parsed.path == "/api/health":
            self._json(200, {"ok": True, "readOnly": READ_ONLY})
        elif parsed.path == "/api/rows":
            query = (parse_qs(parsed.query).get("q") or [""])[0]
            self._json(200, {"ok": True, "rows": search_rows(query)})
        else:
            self._json(404, {"ok": False, "message": "Not found"})

    def do_POST(self) -> None:  # noqa: N802 - required name
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

        try:
            length = int(self.headers.get("Content-Length") or 0)
            request = json.loads(self.rfile.read(length) or b"{}")
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
    """Detach one result, returning it to the name printed on the entry list.

    Nothing is deleted. Releasing the claim lets the row fall back to being
    grouped by its original name, exactly as it was before anyone claimed it -
    which is what makes a mistaken claim safe to undo.
    """
    registry = idn.Registry.load()
    athlete = registry.athletes.get(athlete_id)
    if athlete is None:
        raise LookupError("No such athlete.")

    claim = next((c for c in athlete.claims if c.key == (event, str(race_id))), None)
    if claim is None:
        raise LookupError("That result is not claimed by this athlete.")

    original = claim.name.strip() or "(unnamed)"
    registry.release(athlete_id, event, race_id)

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
