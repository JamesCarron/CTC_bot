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

Exclusions are advisory and recorded with a reason, never silently dropped, so
a wrongly excluded race can be spotted and overridden.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Ordered: the first match wins, so the most specific reason is reported.
_EXCLUSION_RULES: tuple[tuple[str, re.Pattern], ...] = (
    ("template", re.compile(r"\btemplate\b|\bcopy me\b", re.I)),
    ("copy", re.compile(r"\(?\bcopy of\b\)?", re.I)),
    ("test", re.compile(r"\btest\b|\bdemo\b|\bsample\b|\bdummy\b", re.I)),
)

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


def assess_event(stored) -> Verdict:
    """Judge a StoredEvent, using its actual field size."""
    return assess(stored.title, len(stored.event.results))


def partition(stored_events) -> tuple[list, list]:
    """Split events into (included, excluded)."""
    included, excluded = [], []
    for stored in stored_events:
        (included if assess_event(stored).include else excluded).append(stored)
    return included, excluded
