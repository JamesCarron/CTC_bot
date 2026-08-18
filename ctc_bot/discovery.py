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

import re
import time
from dataclasses import dataclass, field

from . import raceclocker as rc
from . import session as sess

EVENT_RESULT_URL = "https://raceclocker.com/Event_Result.php?index={index}"

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
