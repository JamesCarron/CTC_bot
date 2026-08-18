"""Classify a RaceClocker event into a club race format.

The club runs two recurring series that must not be pooled into the same trend
line, because their results are not comparable:

* **Time trial (TT)** - Tuesdays, a single timed leg (start -> finish).
* **Aquathon** - Thursdays, two timed legs (start -> transition -> finish).

Three independent signals are scored and cross-checked rather than trusting any
one of them. Real timing data is messy: a session can be mislabelled, a marshal
can miss a split, or an event can be rescheduled off its usual weekday. When the
signals disagree we return ``UNKNOWN`` with the disagreement recorded, so the
event surfaces for review instead of quietly polluting an athlete's trend.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

TIME_TRIAL = "time_trial"
AQUATHON = "aquathon"
UNKNOWN = "unknown"

# Weekday the series normally runs on.
WEEKDAY_SERIES = {"Tuesday": TIME_TRIAL, "Thursday": AQUATHON}

# Number of timed legs each format produces.
SEGMENT_SERIES = {1: TIME_TRIAL, 2: AQUATHON}

_TITLE_PATTERNS = (
    (AQUATHON, re.compile(r"\baqua(thlon|thon)?\b", re.I)),
    (TIME_TRIAL, re.compile(r"\b(time\s*trial|timetrial|t\.?t\.?)\b", re.I)),
)

_WEEKDAY_RE = re.compile(
    r"\b(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b", re.I
)


@dataclass
class Classification:
    """Outcome of classifying one event, with its supporting evidence."""

    race_type: str
    confident: bool
    signals: dict[str, str | None]
    notes: list[str]

    @property
    def needs_review(self) -> bool:
        return not self.confident


def weekday_of(date_text: str | None) -> str | None:
    """Read the weekday name straight out of the event header text.

    RaceClocker prints it literally (``"Tuesday 18 Aug '26, 19:00"``), so there
    is no calendar arithmetic to get wrong.
    """
    if not date_text:
        return None
    match = _WEEKDAY_RE.search(date_text)
    return match.group(1).capitalize() if match else None


def _from_title(title: str | None) -> str | None:
    if not title:
        return None
    for race_type, pattern in _TITLE_PATTERNS:
        if pattern.search(title):
            return race_type
    return None


def classify(event) -> Classification:
    """Classify an :class:`~ctc_bot.raceclocker.Event`.

    Requires at least two agreeing signals with none dissenting to be
    considered confident.
    """
    weekday = weekday_of(event.date_text)

    signals: dict[str, str | None] = {
        "weekday": WEEKDAY_SERIES.get(weekday) if weekday else None,
        "segments": SEGMENT_SERIES.get(event.segments),
        "title": _from_title(event.title),
    }

    votes = [value for value in signals.values() if value]
    notes: list[str] = []

    if weekday and weekday not in WEEKDAY_SERIES:
        notes.append(f"event ran on a {weekday}, outside the usual Tue/Thu pattern")
    if event.segments not in SEGMENT_SERIES:
        notes.append(
            f"{event.segments} timed leg(s) matches no known format "
            f"({event.timing_points} timing point(s) populated)"
        )

    if not votes:
        notes.append("no usable signal (missing date, title and split structure)")
        return Classification(UNKNOWN, False, signals, notes)

    distinct = set(votes)
    if len(distinct) > 1:
        disagreement = ", ".join(
            f"{key}={value}" for key, value in signals.items() if value
        )
        notes.append(f"signals disagree: {disagreement}")
        return Classification(UNKNOWN, False, signals, notes)

    race_type = distinct.pop()
    confident = len(votes) >= 2 and not notes
    if not confident and not notes:
        notes.append(f"only one signal available ({race_type})")

    return Classification(race_type, confident, signals, notes)
