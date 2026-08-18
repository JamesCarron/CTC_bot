"""Fetch and parse RaceClocker public result pages.

RaceClocker renders results server-side and embeds the full result set as a
JSON array in a top-level ``let AllResults = [...];`` statement, alongside a
handful of scalar ``let Name = "value";`` declarations (Distance, DistanceUnit,
SplitNames, ...).

That means we never need to execute JavaScript or scrape the DOM: we pull the
literals straight out of the HTML source. This module deliberately keeps
fetching and parsing separate so that raw HTML can be archived once and
re-parsed later if the analysis changes.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

import requests

RESULTS_URL = "https://raceclocker.com/{code}"

# Event codes in shared links are 8 lowercase hex chars, e.g. raceclocker.com/7eecd645
EVENT_CODE_RE = re.compile(r"raceclocker\.com/([0-9a-f]{8})\b", re.I)

_PAGE_TITLE_RE = re.compile(
    r'<span[^>]*id=["\']pagetitle["\'][^>]*>(?P<title>[^<]*)</span>', re.I
)
# Header line carries the scheduled date, e.g. "Tuesday 18 Aug '26, 19:00"
_EVENT_DATE_RE = re.compile(
    r"\b(?:Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)day\s+\d{1,2}\s+\w{3,9}\s+'\d{2},\s*\d{2}:\d{2}"
)

# RaceClocker's split slots. Slot 1 is the start gun / chip start, and the
# highest populated slot is the finish. An unused slot is "00:00:00".
MAX_SPLIT_SLOTS = 6
EMPTY_SPLIT = "00:00:00"


class ParseError(RuntimeError):
    """Raised when a page does not look like a RaceClocker results page."""


def extract_event_codes(text: str) -> list[str]:
    """Return de-duplicated, lowercased event codes in first-seen order."""
    seen: dict[str, None] = {}
    for match in EVENT_CODE_RE.finditer(text):
        seen.setdefault(match.group(1).lower(), None)
    return list(seen)


def fetch(code: str, *, timeout: int = 30, session: requests.Session | None = None) -> str:
    """Download the raw HTML for one event code."""
    http = session or requests
    response = http.get(
        RESULTS_URL.format(code=code),
        timeout=timeout,
        headers={"User-Agent": "CTC_bot/0.1 (club results tracker)"},
    )
    response.raise_for_status()
    return response.text


def _extract_js_array(html: str, name: str) -> list[Any]:
    """Pull a ``let <name> = [...]`` array out of the page source.

    Scans with a bracket counter rather than a regex so that nested brackets
    and brackets inside string literals cannot truncate the match early.
    """
    anchor = re.search(rf"\b(?:let|var|const)\s+{re.escape(name)}\s*=\s*\[", html)
    if anchor is None:
        raise ParseError(f"no `{name}` array found in page")

    start = anchor.end() - 1  # position of the opening '['
    depth = 0
    in_string: str | None = None
    escaped = False

    for index in range(start, len(html)):
        char = html[index]
        if in_string is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == in_string:
                in_string = None
            continue
        if char in "\"'":
            in_string = char
        elif char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return json.loads(html[start : index + 1])

    raise ParseError(f"unterminated `{name}` array")


def _extract_js_scalar(html: str, name: str) -> str | None:
    """Pull a ``let <name> = "value";`` string/number literal out of the page."""
    match = re.search(
        rf"\b(?:let|var|const)\s+{re.escape(name)}\s*=\s*(\"([^\"]*)\"|'([^']*)'|[\d.]+)\s*;",
        html,
    )
    if match is None:
        return None
    return match.group(2) or match.group(3) or match.group(1)


@dataclass
class Event:
    """One parsed RaceClocker event."""

    code: str
    title: str | None
    date_text: str | None
    distance: float | None
    distance_unit: str | None
    split_names: list[str] = field(default_factory=list)
    categories: list[dict] = field(default_factory=list)
    results: list[dict] = field(default_factory=list)

    @property
    def timing_points(self) -> int:
        """How many split slots actually carry a time, across the whole field.

        This is the structural fingerprint used to tell race formats apart:
        a time trial records start + finish (2 points, 1 segment), while an
        aquathon records start + transition + finish (3 points, 2 segments).
        """
        used = set()
        for row in self.results:
            for slot in range(1, MAX_SPLIT_SLOTS + 1):
                value = row.get(f"TmSplit{slot}", EMPTY_SPLIT)
                if value and value != EMPTY_SPLIT:
                    used.add(slot)
        return len(used)

    @property
    def segments(self) -> int:
        """Number of timed legs (transitions between timing points)."""
        return max(self.timing_points - 1, 0)

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "title": self.title,
            "date_text": self.date_text,
            "distance": self.distance,
            "distance_unit": self.distance_unit,
            "split_names": self.split_names,
            "categories": self.categories,
            "timing_points": self.timing_points,
            "segments": self.segments,
            "results": self.results,
        }


def parse(html: str, code: str) -> Event:
    """Parse a RaceClocker results page into an :class:`Event`."""
    results = _extract_js_array(html, "AllResults")

    try:
        split_names = _extract_js_array(html, "SplitNames")
    except ParseError:
        split_names = []
    try:
        categories = _extract_js_array(html, "AllCategories")
    except ParseError:
        categories = []

    distance_raw = _extract_js_scalar(html, "Distance")
    try:
        distance = float(distance_raw) if distance_raw else None
    except ValueError:
        distance = None

    title_match = _PAGE_TITLE_RE.search(html)
    date_match = _EVENT_DATE_RE.search(html)

    return Event(
        code=code,
        title=title_match.group("title").strip() if title_match else None,
        date_text=date_match.group(0) if date_match else None,
        distance=distance,
        distance_unit=_extract_js_scalar(html, "DistanceUnit"),
        split_names=split_names,
        categories=categories,
        results=results,
    )


def load(code: str, *, session: requests.Session | None = None) -> Event:
    """Fetch and parse an event in one step."""
    return parse(fetch(code, session=session), code)


def _slot_seconds(row: dict, slot: int) -> float | None:
    """Clock time of one split slot, in seconds past midnight.

    Each slot stores a ``HH:MM:SS`` string plus a companion ``...dc`` field
    holding deci-seconds, which must be included: the time trial's winning
    result (``00:21:14.0``) only reconciles once the ``dc`` digits are applied.
    """
    clock = row.get(f"TmSplit{slot}")
    if not clock or clock == EMPTY_SPLIT:
        return None
    try:
        hours, minutes, seconds = (int(part) for part in clock.split(":"))
    except ValueError:
        return None
    deci = row.get(f"TmSplit{slot}dc") or "0"
    try:
        fraction = int(deci) / 10
    except ValueError:
        fraction = 0.0
    return hours * 3600 + minutes * 60 + seconds + fraction


def populated_slots(row: dict) -> list[int]:
    """Split slots carrying a real time for this athlete, in order."""
    return [
        slot
        for slot in range(1, MAX_SPLIT_SLOTS + 1)
        if _slot_seconds(row, slot) is not None
    ]


def leg_seconds(row: dict) -> list[float]:
    """Duration of each timed leg for one athlete.

    Derived from which slots are *actually populated*, not from ``SplitNames``.
    Those labels are a generic triathlon template and do not track real usage:
    in the sample aquathon the true finish sits in the slot labelled
    "Run start", while slot 6 ("Finish") is empty.
    """
    slots = populated_slots(row)
    legs: list[float] = []
    for start_slot, end_slot in zip(slots, slots[1:]):
        start = _slot_seconds(row, start_slot)
        end = _slot_seconds(row, end_slot)
        if start is None or end is None:
            continue
        if end < start:  # crossed midnight
            end += 24 * 3600
        legs.append(round(end - start, 1))
    return legs


def elapsed_seconds(row: dict) -> float | None:
    """Total elapsed time from first to last populated slot."""
    legs = leg_seconds(row)
    return round(sum(legs), 1) if legs else None


def ranked(results: list[dict]) -> list[dict]:
    """Return finishers sorted by actual time, with a recomputed ``Position``.

    RaceClocker's own ``Rank`` field mirrors bib order in both sample events and
    must not be used. Non-finishers (DNF/DNS, or no numeric result) are excluded.
    """
    finishers = []
    for row in results:
        try:
            row_seconds = float(row["TmResultSec"])
        except (KeyError, TypeError, ValueError):
            continue
        if row_seconds <= 0:
            continue
        finishers.append((row_seconds, row))

    finishers.sort(key=lambda pair: pair[0])
    return [dict(row, Position=position) for position, (_, row) in enumerate(finishers, 1)]
