"""Refresh the store on a weekly rhythm, from inside the running server.

The club races on Tuesday (time trial) and Thursday (aquathon), so the store is
refreshed on Wednesday and Friday mornings - late enough that the timekeeper has
published, early enough that the results are there before anyone looks.

A thread inside the existing process rather than a second container or a host
timer: one moving part, and Arcane's healthcheck already watches it. If a run
fails the thread survives and tries again at the next slot; a network blip must
not silently stop all future refreshes.

Times are local to ``TZ``. Without it set the container runs on UTC and the
schedule drifts an hour across the year.
"""

from __future__ import annotations

import threading
import time
import traceback
from datetime import datetime, timedelta

# Weekday numbers as datetime reports them (Monday = 0).
WEDNESDAY, FRIDAY = 2, 4
DEFAULT_DAYS = (WEDNESDAY, FRIDAY)
DEFAULT_HOUR = 7


def next_run(after: datetime, *, days=DEFAULT_DAYS, hour: int = DEFAULT_HOUR) -> datetime:
    """The next scheduled moment strictly after ``after``."""
    candidate = after.replace(hour=hour, minute=0, second=0, microsecond=0)
    if candidate <= after:
        candidate += timedelta(days=1)
    for _ in range(8):
        if candidate.weekday() in days:
            return candidate
        candidate += timedelta(days=1)
    raise ValueError(f"No weekday in {days!r} is reachable.")


def refresh() -> str:
    """Pull anything new from the admin console and rebuild the dashboard."""
    from . import dashboard, discovery, session, store

    http = session.login()
    listings = discovery.fetch_listing(session=http)
    already = {stored.code for stored in store.load_all()}

    added = 0
    failed = 0
    for listing in listings:
        try:
            found = discovery.resolve(listing, session=http)
        except Exception:
            failed += 1
            continue
        if found.code in already:
            continue
        store.save(
            found.html,
            found.code,
            extra={
                "index": listing.index,
                "start_type": listing.start_type,
                "intermediate_splits": listing.intermediate_splits,
                "listed_distance_km": listing.distance_km,
                "participants": listing.participants,
                "locked": listing.locked,
            },
        )
        added += 1
        time.sleep(0.5)  # the same courtesy the backfill script shows

    dashboard.build()
    return f"{added} new event(s), {failed} unresolved, {len(listings)} listed"


class Scheduler:
    """Runs :func:`refresh` on the weekly slots, in a daemon thread."""

    def __init__(self, *, days=DEFAULT_DAYS, hour: int = DEFAULT_HOUR, run_now: bool = False):
        self.days = days
        self.hour = hour
        self.run_now = run_now
        self.last_result: str | None = None
        self.last_run: datetime | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="ctc-refresh", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        if self.run_now:
            self._run_once()
        while not self._stop.is_set():
            due = next_run(datetime.now(), days=self.days, hour=self.hour)
            print(f"[refresh] next run {due:%a %d %b %H:%M}")
            # Wake periodically rather than sleeping until the target, so a
            # suspended or clock-adjusted host cannot overshoot the slot.
            while not self._stop.is_set() and datetime.now() < due:
                self._stop.wait(min(300, max(1, (due - datetime.now()).total_seconds())))
            if self._stop.is_set():
                return
            self._run_once()

    def _run_once(self) -> None:
        started = datetime.now()
        print(f"[refresh] starting {started:%Y-%m-%d %H:%M}")
        try:
            self.last_result = refresh()
            print(f"[refresh] done: {self.last_result}")
        except Exception:
            # One failed refresh must not end every future one.
            self.last_result = "failed - see log"
            print("[refresh] FAILED:")
            traceback.print_exc()
        self.last_run = started
