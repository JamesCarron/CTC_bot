"""Discover the club's events from the RaceClocker admin console.

``My_Events.php`` lists every event in the account. It uses two layouts for the
same data - recent events render as ``EventCard`` tiles, older ones as compact
``EventList`` rows - so both are parsed into one shape.

The listing identifies each event only by a positional ``index``, not by the
8-hex code that public result pages use. That index is **not stable**: it is a
position in the account's event list and shifts as events are added or removed.
So an index is only ever used to fetch ``Event_Result.php?index=N``, which
returns both the durable public code and the full ``AllResults`` payload in one
authenticated request. The code is what gets stored.

Fetching results this way also reaches events that are locked or unpublished,
which the public URL would not serve.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from . import raceclocker as rc
from . import session as sess

EVENT_RESULT_URL = "https://raceclocker.com/Event_Result.php?index={index}"

CACHE_PATH = Path(__file__).resolve().parent.parent / "data" / "listing_cache.json"

# An event with no public code is usually one that was never published, and that
# does not change. Re-checking every one of them on every sweep is the bulk of
# the wasted work - but "never" is too strong, since an event can be published
# later, so they are retried occasionally instead.
UNPUBLISHED_RETRY_DAYS = 30

# Each entry begins with one of these; splitting on them keeps one entry's
# fields from bleeding into the next.
_ENTRY_SPLIT_RE = re.compile(r'<div class="(EventCard|EventList)" id=(\d+)\s*>')

_TITLE_RES = (
    re.compile(r'class="Type_black_12 NoWrap"[^>]*>(?:<img[^>]*>)?\s*([^<]+)'),
    re.compile(r'class="Type_black_10 EventListTitle">\s*([^<]+)'),
)
_DATE_RES = (
    re.compile(r'class="Type_grey_8 EventListDate">\s*([^<]+)'),
    re.compile(r'&nbsp;&nbsp;\s*([0-9]{1,2} \w{3} \'[0-9]{2}[^<]*)'),
)
_COUNT_RE = re.compile(r'id="count_\d+"[^>]*>(?:<img[^>]*>)?\s*(\d+)')
_SPORT_ICON_RE = re.compile(r"icon_sports_(\w+?)_dark\.svg")

# "13 km Cycling time trial" / "8 km Other"
_METADATA_RE = re.compile(
    r'class="(?:Type_black_10 NoWrap VerticalSpacerQuarter|Type_grey_8 EventListMetaData NoWrap)"[^>]*>'
    r"\s*([^<]+)"
)
# "Time trial" (individual starts) or "Mass start (1 split)"
_START_TYPE_RES = (
    re.compile(r'<span class="Type_grey_10">\s*([^<]+)'),
    re.compile(
        r'class="Type_grey_8 EventListMetaData NoWrap">\s*((?:Time trial|Mass start)[^<]*)'
    ),
)
_SPLIT_COUNT_RE = re.compile(r"\((\d+)\s+split", re.I)
_DISTANCE_RE = re.compile(r"([\d.]+)\s*(km|mi|m)\b", re.I)


def _first(patterns, text: str) -> str | None:
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            return match.group(1).strip()
    return None


@dataclass
class Listing:
    """One event as advertised on the admin listing page."""

    index: int
    layout: str
    title: str | None = None
    date_text: str | None = None
    participants: int | None = None
    metadata: str | None = None
    start_type: str | None = None
    sport_icon: str | None = None
    locked: bool = False

    @property
    def distance_km(self) -> float | None:
        """Distance as advertised. Not trusted - see ctc_bot.config."""
        if not self.metadata:
            return None
        match = _DISTANCE_RE.search(self.metadata)
        if not match:
            return None
        value = float(match.group(1))
        unit = match.group(2).lower()
        return value * 1.60934 if unit == "mi" else (value / 1000 if unit == "m" else value)

    @property
    def intermediate_splits(self) -> int | None:
        """Timing points between start and finish, if the listing states them.

        ``"Mass start (1 split)"`` means one intermediate point, so two timed
        legs. A time trial states no split count and has none.
        """
        if not self.start_type:
            return None
        match = _SPLIT_COUNT_RE.search(self.start_type)
        if match:
            return int(match.group(1))
        return 0 if self.start_type.lower().startswith("time trial") else None

    @property
    def mass_start(self) -> bool | None:
        if not self.start_type:
            return None
        return self.start_type.lower().startswith("mass start")

    @property
    def result_url(self) -> str:
        return EVENT_RESULT_URL.format(index=self.index)


def parse_listing(html: str) -> list[Listing]:
    """Parse every event advertised on ``My_Events.php``."""
    parts = _ENTRY_SPLIT_RE.split(html)
    # parts = [preamble, layout, id, chunk, layout, id, chunk, ...]
    listings: list[Listing] = []
    for offset in range(1, len(parts) - 2, 3):
        layout, index, chunk = parts[offset], parts[offset + 1], parts[offset + 2]
        metadata = _METADATA_RE.search(chunk)
        listings.append(
            Listing(
                index=int(index),
                layout=layout,
                title=_first(_TITLE_RES, chunk),
                date_text=_first(_DATE_RES, chunk),
                participants=int(m.group(1)) if (m := _COUNT_RE.search(chunk)) else None,
                metadata=metadata.group(1).strip() if metadata else None,
                start_type=_first(_START_TYPE_RES, chunk),
                sport_icon=(
                    s.group(1) if (s := _SPORT_ICON_RE.search(chunk)) else None
                ),
                locked="EventCardLockIcon" in chunk,
            )
        )
    return listings


def fetch_listing(*, session=None) -> list[Listing]:
    """Log in if needed and parse the admin event list."""
    return parse_listing(sess.fetch_event_list(session=session))


@dataclass
class Discovered:
    """A listing resolved to its durable public code, with the results page."""

    listing: Listing
    code: str
    html: str


def resolve(listing: Listing, *, session=None) -> Discovered:
    """Fetch one event's admin result page and pull out its public code.

    Works for locked and unpublished events, which the public URL will not serve.
    """
    html = sess.fetch(listing.result_url, session=session)
    codes = rc.extract_event_codes(html)
    if not codes:
        raise LookupError(
            f"No public event code found on the result page for index {listing.index} "
            f"({listing.title!r}). The event may never have been published."
        )
    return Discovered(listing=listing, code=codes[0], html=html)


class ListingCache:
    """Remembers which public code each listing resolved to.

    The sweep's expensive part is that a listing identifies an event only by a
    positional index, so learning its durable code costs one authenticated
    request each - 228 of them every sweep, to discover that 209 are already
    stored.

    The index cannot be the cache key: it is a position in the account's event
    list and shifts whenever an event is added or removed. Title and date
    together are stable, and are what a human would use to say "that is the
    same race".

    A key that matches more than one listing in a single run is never trusted -
    two events genuinely sharing a title and date exist in this account, and
    guessing between them would attach one event's results to the other.
    """

    def __init__(self, entries: dict | None = None):
        self.entries: dict[str, dict] = entries or {}

    @staticmethod
    def key(listing: "Listing") -> str:
        title = re.sub(r"\s+", " ", (listing.title or "").strip()).casefold()
        return f"{(listing.date_text or '').strip()}|{title}"

    @classmethod
    def load(cls, path: Path | None = None) -> "ListingCache":
        target = path or CACHE_PATH
        if not target.exists():
            return cls()
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return cls()  # a corrupt cache costs a slow sweep, never wrong data
        return cls(payload.get("entries", {}))

    def save(self, path: Path | None = None) -> Path:
        target = path or CACHE_PATH
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps({"version": 1, "entries": self.entries}, indent=2), encoding="utf-8"
        )
        return target

    def record(self, listing: "Listing", code: str | None) -> None:
        self.entries[self.key(listing)] = {
            "code": code,
            "checked": datetime.now().isoformat(timespec="seconds"),
        }

    def lookup(self, listing: "Listing") -> tuple[bool, str | None]:
        """Returns ``(is_a_usable_hit, code)``."""
        entry = self.entries.get(self.key(listing))
        if entry is None:
            return False, None

        if entry.get("code"):
            return True, entry["code"]

        # Known unpublished: honour it, but not forever.
        try:
            checked = datetime.fromisoformat(entry.get("checked", ""))
        except ValueError:
            return False, None
        if datetime.now() - checked > timedelta(days=UNPUBLISHED_RETRY_DAYS):
            return False, None
        return True, None

    def ambiguous_keys(self, listings) -> set[str]:
        """Keys shared by more than one listing in this run."""
        seen: dict[str, int] = {}
        for listing in listings:
            key = self.key(listing)
            seen[key] = seen.get(key, 0) + 1
        return {key for key, count in seen.items() if count > 1}


def resolve_all(
    listings,
    *,
    session=None,
    skip_codes: set[str] | None = None,
    delay: float = 0.5,
    limit: int | None = None,
    on_event=None,
) -> tuple[list[Discovered], list[tuple[Listing, str]]]:
    """Resolve many listings, politely.

    Returns ``(discovered, failures)``. Failures are collected rather than
    raised so one unpublished event cannot abort a whole backfill.

    ``delay`` spaces out requests - this walks the club's entire history, so it
    should not hammer RaceClocker.
    """
    http = session or sess.login()
    discovered: list[Discovered] = []
    failures: list[tuple[Listing, str]] = []
    skip = skip_codes or set()

    for position, listing in enumerate(listings):
        if limit is not None and len(discovered) >= limit:
            break
        try:
            found = resolve(listing, session=http)
        except Exception as exc:  # keep going through a long backfill
            failures.append((listing, str(exc)))
            if on_event:
                on_event(listing, None, str(exc))
            continue

        if found.code in skip:
            if on_event:
                on_event(listing, found.code, "already stored")
            continue

        discovered.append(found)
        if on_event:
            on_event(listing, found.code, None)
        if delay and position < len(listings) - 1:
            time.sleep(delay)

    return discovered, failures
