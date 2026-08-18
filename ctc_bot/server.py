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
        if self.path in ("/", "/index.html", "/dashboard.html"):
            path = dashboard.build()
            self._send(200, path.read_bytes(), "text/html; charset=utf-8")
        elif self.path == "/api/health":
            self._json(200, {"ok": True})
        else:
            self._json(404, {"ok": False, "message": "Not found"})

    def do_POST(self) -> None:  # noqa: N802 - required name
        if self.path != "/api/claim":
            self._json(404, {"ok": False, "message": "Not found"})
            return

        try:
            length = int(self.headers.get("Content-Length") or 0)
            request = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, TypeError):
            self._json(400, {"ok": False, "message": "Could not read the request."})
            return

        name = (request.get("name") or "").strip()
        athlete_id = (request.get("athleteId") or "").strip()
        if not name or not athlete_id:
            self._json(400, {"ok": False, "message": "A name and an athlete are both required."})
            return

        try:
            message = claim_athlete(athlete_id, name)
        except LookupError as exc:
            self._json(404, {"ok": False, "message": str(exc)})
            return

        self._json(200, {"ok": True, "message": message})


def claim_athlete(athlete_id: str, display_name: str) -> str:
    """Record every row currently attributed to ``athlete_id`` under ``display_name``.

    The dashboard groups unclaimed athletes provisionally by exact name; this
    turns one of those groups into a real claim, which then teaches the spelling
    for future events.
    """
    events = store.load_all()
    registry = idn.Registry.load()

    from . import curation

    included, _ = curation.partition(events)
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
