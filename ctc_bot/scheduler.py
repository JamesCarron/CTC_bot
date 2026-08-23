"""Keep the store current, from inside the running server.

Two rhythms, because they answer different questions.

**Race-night polling.** The club races Tuesday (time trial) and Thursday
(aquathon), starting at 19:00 and finishing around 20:00-21:00. From
``WINDOW_START`` the store is checked every ``POLL_MINUTES`` until tonight's
results appear, then polling stops for the night. Results are usually up within
minutes of the last finisher, so this puts them on the dashboard while people
are still in the car park.

**A morning sweep** on Wednesday and Friday, and it is not redundant: it catches
a night where the timekeeper published late, the window was missed, or the
server was down. Race-night polling is the fast path; this is the one that
guarantees nothing is silently lost.

The stop condition is *results exist*, not *an event exists*. Events are created
in the admin console **before** the race - the sample time trial was in the
listing with an empty field days ahead - so "a new event appeared" would stop
polling before a single time was recorded. Tonight's event is therefore
re-fetched on every poll, overwriting what is stored, until it actually has
finishers.

Times are local to ``TZ``. Without it the container runs on UTC and every window
is an hour out for half the year.
"""

from __future__ import annotations

import os
import threading
import time
import traceback
from datetime import date, datetime, timedelta

# Weekday numbers as datetime reports them (Monday = 0).
TUESDAY, WEDNESDAY, THURSDAY, FRIDAY = 1, 2, 3, 4

# Race nights, and the mornings that sweep up after them.
RACE_DAYS = (TUESDAY, THURSDAY)
SWEEP_DAYS = (WEDNESDAY, FRIDAY)

WINDOW_START = int(os.environ.get("CTC_POLL_FROM_HOUR", "19"))
WINDOW_END = int(os.environ.get("CTC_POLL_UNTIL_HOUR", "23"))
POLL_MINUTES = int(os.environ.get("CTC_POLL_MINUTES", "5"))
SWEEP_HOUR = int(os.environ.get("CTC_SWEEP_HOUR", "7"))


def is_race_night(when: datetime) -> bool:
    """Whether ``when`` falls in a race evening's polling window."""
    return when.weekday() in RACE_DAYS and WINDOW_START <= when.hour < WINDOW_END


def next_sweep(after: datetime, *, days=SWEEP_DAYS, hour: int = SWEEP_HOUR) -> datetime:
    """The next morning sweep strictly after ``after``."""
    candidate = after.replace(hour=hour, minute=0, second=0, microsecond=0)
    if candidate <= after:
        candidate += timedelta(days=1)
    for _ in range(8):
        if candidate.weekday() in days:
            return candidate
        candidate += timedelta(days=1)
    raise ValueError(f"No weekday in {days!r} is reachable.")


def next_window_start(after: datetime, *, days=RACE_DAYS, hour: int = WINDOW_START) -> datetime:
    """The start of the next race-night window strictly after ``after``."""
    candidate = after.replace(hour=hour, minute=0, second=0, microsecond=0)
    if candidate <= after:
        candidate += timedelta(days=1)
    for _ in range(8):
        if candidate.weekday() in days:
            return candidate
        candidate += timedelta(days=1)
    raise ValueError(f"No weekday in {days!r} is reachable.")


def next_wakeup(after: datetime) -> datetime:
    """Whichever comes first: the next race window or the next morning sweep."""
    return min(next_window_start(after), next_sweep(after))


# ---- the work ------------------------------------------------------------


def _listing_extra(listing) -> dict:
    return {
        "index": listing.index,
        "start_type": listing.start_type,
        "intermediate_splits": listing.intermediate_splits,
        "listed_distance_km": listing.distance_km,
        "participants": listing.participants,
        "locked": listing.locked,
    }


def _finishers(stored) -> int:
    from . import curation

    return curation.finisher_count(stored)


def poll_tonight(today: date | None = None) -> tuple[bool, str]:
    """Re-fetch today's events. Returns ``(results_are_in, description)``.

    Today's events are fetched even when already stored, because an event
    created before the race is stored with an empty field and only gains times
    afterwards.
    """
    from . import dashboard, discovery, metrics, session, store

    today = today or date.today()
    http = session.login()
    listings = discovery.fetch_listing(session=http)

    todays = [l for l in listings if metrics.parse_date(l.date_text) == today]
    if not todays:
        return False, f"nothing listed for {today}"

    found = 0
    total = 0
    for listing in todays:
        try:
            resolved = discovery.resolve(listing, session=http)
        except Exception:
            continue
        stored = store.save(resolved.html, resolved.code, extra=_listing_extra(listing))
        timed = _finishers(stored)
        total += timed
        if timed:
            found += 1

    if found:
        dashboard.build_if_stale()
        return True, f"{found} event(s) with {total} result(s) for {today}"
    return False, f"{len(todays)} event(s) listed for {today}, none timed yet"


def sweep() -> str:
    """Pull anything not already stored, and rebuild.

    A listing names an event only by a positional index, so learning its durable
    code costs one authenticated request. Resolving all 228 every sweep, to find
    that 209 are already stored, is the bulk of the work - so a cache maps each
    listing to the code it resolved to last time, and only genuinely unknown
    listings are fetched.
    """
    from . import dashboard, discovery, session, store

    http = session.login()
    listings = discovery.fetch_listing(session=http)
    already = {stored.code for stored in store.load_all()}

    cache = discovery.ListingCache.load()
    # A title+date shared by two listings cannot identify either of them, so
    # those are always resolved properly rather than guessed at.
    ambiguous = cache.ambiguous_keys(listings)

    added = failed = skipped = fetched = 0
    for listing in listings:
        if discovery.ListingCache.key(listing) not in ambiguous:
            hit, code = cache.lookup(listing)
            # Skip only when the cache says we already hold it, or that it has
            # no public code. A cached code we have NOT stored still needs
            # fetching - that is the new-event case the sweep exists for.
            if hit and (code is None or code in already):
                skipped += 1
                continue

        try:
            resolved = discovery.resolve(listing, session=http)
            fetched += 1
        except Exception:
            cache.record(listing, None)
            failed += 1
            continue

        cache.record(listing, resolved.code)
        if resolved.code in already:
            continue
        store.save(resolved.html, resolved.code, extra=_listing_extra(listing))
        added += 1
        time.sleep(0.5)  # the same courtesy the backfill script shows

    cache.save()
    dashboard.build_if_stale()
    return (
        f"{added} new event(s); {fetched} fetched, {skipped} from cache, "
        f"{failed} unresolved, {len(listings)} listed"
    )


# Kept for callers that just want "bring everything up to date".
refresh = sweep


class Scheduler:
    """Race-night polling plus a morning sweep, in one daemon thread."""

    def __init__(self, *, poll_minutes: int = POLL_MINUTES):
        self.poll_minutes = poll_minutes
        self.last_result: str | None = None
        self.last_run: datetime | None = None
        # The date whose results are already in, so polling stops for the night
        # rather than hammering the admin console until the window closes.
        self.settled_on: date | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="ctc-refresh", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    # ---- loop --------------------------------------------------------

    def _loop(self) -> None:
        print(
            f"[refresh] race nights Tue/Thu {WINDOW_START:02d}:00-{WINDOW_END:02d}:00 "
            f"every {self.poll_minutes}m; sweep Wed/Fri {SWEEP_HOUR:02d}:00"
        )
        while not self._stop.is_set():
            now = datetime.now()

            if is_race_night(now) and self.settled_on != now.date():
                self._poll(now)
                self._stop.wait(self.poll_minutes * 60)
                continue

            due = next_wakeup(now)
            print(f"[refresh] next check {due:%a %d %b %H:%M}")
            self._sleep_until(due)
            if self._stop.is_set():
                return
            # A sweep slot fires the sweep; a window start just re-enters the
            # loop, which then takes the polling branch.
            fired = datetime.now()
            if fired.weekday() in SWEEP_DAYS and fired.hour == SWEEP_HOUR:
                self._sweep()

    def _sleep_until(self, due: datetime) -> None:
        """Wake periodically rather than sleeping the whole way.

        A suspended host, or a clock correction, would otherwise overshoot the
        slot entirely and skip a night.
        """
        while not self._stop.is_set() and datetime.now() < due:
            remaining = (due - datetime.now()).total_seconds()
            self._stop.wait(min(300, max(1, remaining)))

    # ---- actions -----------------------------------------------------

    def _poll(self, now: datetime) -> None:
        try:
            settled, detail = poll_tonight(now.date())
            self.last_result = detail
            if settled:
                self.settled_on = now.date()
                print(f"[refresh] results in - {detail}. Polling stops for tonight.")
            else:
                print(f"[refresh] {detail}; checking again in {self.poll_minutes}m")
        except Exception:
            # One failed poll must not end the night's checking.
            self.last_result = "poll failed - see log"
            print("[refresh] poll FAILED:")
            traceback.print_exc()
        self.last_run = now

    def _sweep(self) -> None:
        started = datetime.now()
        print(f"[refresh] sweep starting {started:%Y-%m-%d %H:%M}")
        try:
            self.last_result = self.sweep_once()
            print(f"[refresh] sweep done: {self.last_result}")
        except Exception:
            self.last_result = "sweep failed - see log"
            print("[refresh] sweep FAILED:")
            traceback.print_exc()
        self.last_run = started

    @staticmethod
    def sweep_once() -> str:
        return sweep()
