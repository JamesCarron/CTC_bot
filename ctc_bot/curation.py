"""Decide which events belong in an athlete's trend.

Seven years of a live RaceClocker account contains more than races. Backfilling
the club's history turned up, on 23 separate dates, several events sharing one
date - and inspecting them showed four kinds of non-race:

* **Copies** - ``"(Copy of) CTC Time Trial 15th September 2020"`` alongside the
  real one, with an overlapping field. Counting both would give every athlete a
  phantom duplicate race.
* **Templates** - ``"Aquathon TEMPLATE COPY ME"``, no participants.
* **Tests and demos** - ``"TT Test Demo"``, ``"test Aquathon"``, carrying
  made-up times.
* **Empty shells** - created and never used.

Genuinely distinct races *do* share dates - a 5 km and a 10 km club race on one
morning, or the 20 km and 40 km legs of the same event - so a date collision is
never on its own grounds for exclusion. Only the event's own title and field
size are used.

Beyond non-races, three further kinds of event are excluded by decision, because
they cannot share a trend line with the two recurring series:

* **Track / running time trials** - the Wednesday 3 km and 5 km track series
  ("5k Track TT", "RUNNING TT", "New to Tri"). A real series of its own, but a
  5 km run is not comparable with a 13 km bike time trial.
* **One-off club events** - LaGrandeCourse (20 km and 40 km cycles, 1500 m
  swim), the 5 km and 10 km club races, training events and away races. Each
  happens once, so there is nothing to trend against.
* **Untimed events** - a start list where nobody was ever timed. Several exist
  with full fields and no results at all.

Exclusions are advisory and recorded with a reason, never silently dropped, so
a wrongly excluded race can be spotted and overridden.

Field-relative statistics need a real field: see ``MIN_FIELD_FOR_STATS``.
Small races are still kept and still show raw times and pace - only the
comparative statistics are suppressed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Ordered: the first match wins, so the most specific reason is reported.
_EXCLUSION_RULES: tuple[tuple[str, re.Pattern], ...] = (
    ("template", re.compile(r"\btemplate\b|\bcopy me\b", re.I)),
    ("copy", re.compile(r"\(?\bcopy of\b\)?", re.I)),
    ("test", re.compile(r"\btest\b|\bdemo\b|\bsample\b|\bdummy\b", re.I)),
    # The Wednesday track/running series - a different discipline entirely.
    (
        "track/running series",
        re.compile(r"\btrack\b|\brunning\b|\bntt\b|\bnew to tri\b", re.I),
    ),
    # One-off club events with no recurring series to trend against.
    (
        "one-off club event",
        re.compile(r"lagrandecourse|\bclub race\b|\btraining event\b|\baway race\b", re.I),
    ),
)

# Below this many finishers, a z-score or percentile is noise dressed as signal.
# The race is still kept; only its field-relative statistics are suppressed.
MIN_FIELD_FOR_STATS = 5

# The club cycling time trial measures 13-14 km depending on the route used.
# Anything appreciably shorter is a different event wearing the words
# "time trial".
MIN_TT_DISTANCE_KM = 10.0

# A default name RaceClocker gives an event nobody renamed. Not excluded on its
# own - plenty are real races - but worth surfacing.
_UNNAMED_RE = re.compile(r"^\s*new race\b", re.I)


@dataclass
class Verdict:
    """Whether an event should count towards trends, and why."""

    include: bool
    reason: str = ""

    @property
    def excluded(self) -> bool:
        return not self.include


def assess(title: str | None, participants: int | None = None) -> Verdict:
    """Judge one event from its title and field size."""
    name = (title or "").strip()

    if not name:
        return Verdict(False, "untitled")

    for reason, pattern in _EXCLUSION_RULES:
        if pattern.search(name):
            return Verdict(False, reason)

    if participants is not None and participants == 0:
        return Verdict(False, "no participants")

    if _UNNAMED_RE.match(name):
        return Verdict(True, "unnamed (default title) - worth checking")

    return Verdict(True)


def finisher_count(stored) -> int:
    """Entrants with an actual recorded time."""
    total = 0
    for row in stored.event.results:
        try:
            if float(row.get("TmResultSec")) > 0:
                total += 1
        except (TypeError, ValueError):
            continue
    return total


def assess_event(stored) -> Verdict:
    """Judge a StoredEvent, using its real field and whether anyone was timed."""
    verdict = assess(stored.title, len(stored.event.results))
    if verdict.excluded:
        return verdict

    if finisher_count(stored) == 0:
        return Verdict(False, "no recorded times")

    # The club's cycling time trial runs 13-14 km. A much shorter "time trial"
    # belongs to the Wednesday running series, which does not always say so in
    # its title (e.g. "29th Jun 2022 - 5km Time Trial").
    listed = stored.listing.get("listed_distance_km")
    if stored.race_type == "time_trial" and listed and listed < MIN_TT_DISTANCE_KM:
        return Verdict(False, "not the club time trial course")

    return verdict


def has_reliable_field_stats(stored) -> bool:
    """Whether this race has enough finishers for z-score and percentile."""
    return finisher_count(stored) >= MIN_FIELD_FOR_STATS


def partition(stored_events) -> tuple[list, list]:
    """Split events into (included, excluded)."""
    included, excluded = [], []
    for stored in stored_events:
        (included if assess_event(stored).include else excluded).append(stored)
    return included, excluded
