"""Local web server for the dashboard and the claim form.

Serves the generated page and one small API the page calls to record a claim.
Claims have to write to ``data/identity.json``, which a ``file://`` page cannot
do - that is the only reason a server exists.

It binds to localhost only. Nothing here is exposed to the network, and there
is no authentication, so it must stay that way.
"""

from __future__ import annotations

import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import dashboard
from . import identity as idn
from . import store

HOST = "127.0.0.1"
DEFAULT_PORT = 8777


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
            path = dashboard.build()
            self._send(200, path.read_bytes(), "text/html; charset=utf-8")
        elif parsed.path == "/api/health":
            self._json(200, {"ok": True})
        elif parsed.path == "/api/rows":
            query = (parse_qs(parsed.query).get("q") or [""])[0]
            self._json(200, {"ok": True, "rows": search_rows(query)})
        else:
            self._json(404, {"ok": False, "message": "Not found"})

    def do_POST(self) -> None:  # noqa: N802 - required name
        if self.path not in ("/api/claim", "/api/adopt", "/api/disown"):
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
            else:
                if not (athlete_id and event and race_id):
                    self._json(
                        400,
                        {"ok": False, "message": "An athlete, event and result are all required."},
                    )
                    return
                message = (
                    adopt_row(athlete_id, event, race_id)
                    if self.path == "/api/adopt"
                    else disown_row(athlete_id, event, race_id)
                )
        except LookupError as exc:
            self._json(404, {"ok": False, "message": str(exc)})
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


def serve(port: int = DEFAULT_PORT, *, open_browser: bool = True) -> None:
    """Run the dashboard server until interrupted."""
    dashboard.build()

    httpd = ThreadingHTTPServer((HOST, port), _Handler)
    url = f"http://{HOST}:{port}/"

    print("CTC_bot dashboard")
    print("=" * 52)
    print(f"  {url}")
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
