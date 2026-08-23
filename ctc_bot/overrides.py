"""Local corrections layered over the archived RaceClocker data.

Two things the club needs that RaceClocker cannot give:

* **A corrected time.** Hand timing misfires. When the sheet says 25:37 and the
  rider's own computer says 24:58, the club should be able to say so.
* **A race that was never recorded at all.** Someone rides, the timer misses
  them, and the result simply is not in the system.

Both are kept **outside** the archived pages, in ``data/overrides.json``. The
raw HTML stays exactly as RaceClocker served it, so a correction can always be
undone and re-parsing the whole history never destroys one. Nothing here edits
an event file.

A correction is applied *before* anything is computed, so the field mean,
finishing positions and z-scores all reflect it consistently - a corrected time
that left everyone else's z-score measured against the wrong mean would be worse
than no correction at all. Every corrected or added result carries a flag
through to the dashboard, where it is marked and can be reset.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path

OVERRIDES_PATH = Path(__file__).resolve().parent.parent / "data" / "overrides.json"

# Marks a manually added result's synthetic event code, so it can never collide
# with a real RaceClocker 8-hex code.
MANUAL_PREFIX = "manual:"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass
class Edit:
    """A corrected finish time for one existing result row."""

    event: str
    race_id: str
    seconds: float
    original_seconds: float
    note: str = ""
    at: str = field(default_factory=_now)

    @property
    def key(self) -> tuple[str, str]:
        return (self.event, str(self.race_id))


@dataclass
class Addition:
    """A race result that was never recorded by the timing system."""

    id: str
    athlete_id: str
    race_type: str
    route: str | None
    when: str  # ISO date
    seconds: float
    title: str = "Added by hand"
    note: str = ""
    at: str = field(default_factory=_now)

    @property
    def event_code(self) -> str:
        return f"{MANUAL_PREFIX}{self.id}"


class Overrides:
    """Every local correction, loaded from and saved to one file."""

    def __init__(self, edits: list[Edit] | None = None, additions: list[Addition] | None = None):
        self.edits: dict[tuple[str, str], Edit] = {e.key: e for e in (edits or [])}
        self.additions: list[Addition] = list(additions or [])

    # ---- persistence -------------------------------------------------

    @classmethod
    def load(cls, path: Path | None = None) -> "Overrides":
        target = path or OVERRIDES_PATH
        if not target.exists():
            return cls()
        payload = json.loads(target.read_text(encoding="utf-8"))
        return cls(
            edits=[Edit(**e) for e in payload.get("edits", [])],
            additions=[Addition(**a) for a in payload.get("additions", [])],
        )

    def save(self, path: Path | None = None) -> Path:
        target = path or OVERRIDES_PATH
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(
                {
                    "version": 1,
                    "edits": [asdict(e) for e in self.edits.values()],
                    "additions": [asdict(a) for a in self.additions],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return target

    # ---- corrections -------------------------------------------------

    def edit_time(self, event: str, race_id: str, seconds: float, original: float, note: str = "") -> Edit:
        """Record a corrected time, or update one already recorded.

        ``original`` is always the time RaceClocker published, never the
        previously corrected value, so resetting returns to the source no matter
        how many times a result has been amended.
        """
        if seconds <= 0:
            raise ValueError("A corrected time must be greater than zero.")
        key = (event, str(race_id))
        existing = self.edits.get(key)
        self.edits[key] = Edit(
            event=event,
            race_id=str(race_id),
            seconds=float(seconds),
            original_seconds=existing.original_seconds if existing else float(original),
            note=note,
        )
        return self.edits[key]

    def reset_time(self, event: str, race_id: str) -> Edit | None:
        """Drop a correction, restoring the published time."""
        return self.edits.pop((event, str(race_id)), None)

    def edit_for(self, event: str, race_id: str) -> Edit | None:
        return self.edits.get((event, str(race_id)))

    # ---- additions ---------------------------------------------------

    def add_result(
        self,
        athlete_id: str,
        race_type: str,
        when: str,
        seconds: float,
        *,
        route: str | None = None,
        title: str = "Added by hand",
        note: str = "",
    ) -> Addition:
        if seconds <= 0:
            raise ValueError("A time must be greater than zero.")
        try:
            date.fromisoformat(when)
        except ValueError:
            raise ValueError(f"Could not read the date {when!r}. Use YYYY-MM-DD.") from None

        addition = Addition(
            id=uuid.uuid4().hex[:8],
            athlete_id=athlete_id,
            race_type=race_type,
            route=route,
            when=when,
            seconds=float(seconds),
            title=title.strip() or "Added by hand",
            note=note,
        )
        self.additions.append(addition)
        return addition

    def remove_result(self, addition_id: str) -> Addition | None:
        for index, addition in enumerate(self.additions):
            if addition.id == addition_id:
                return self.additions.pop(index)
        return None

    def additions_for(self, athlete_id: str) -> list[Addition]:
        return [a for a in self.additions if a.athlete_id == athlete_id]

    # ---- applying ----------------------------------------------------

    def apply_times(self, stored_events) -> list:
        """Return the events with corrected times substituted in.

        Copies rather than mutating, so the objects the store handed out - and
        the archived files behind them - are left alone.
        """
        if not self.edits:
            return list(stored_events)

        from copy import copy

        by_event: dict[str, list[Edit]] = {}
        for edit in self.edits.values():
            by_event.setdefault(edit.event, []).append(edit)

        applied = []
        for stored in stored_events:
            edits = by_event.get(stored.code)
            if not edits:
                applied.append(stored)
                continue

            wanted = {e.race_id: e for e in edits}
            rows = []
            for row in stored.event.results:
                edit = wanted.get(str(row.get("RaceID")))
                if edit is None:
                    rows.append(row)
                    continue
                corrected = dict(row)
                corrected["TmResultSec"] = f"{edit.seconds:.1f}"
                corrected["_edited"] = True
                rows.append(corrected)

            event = copy(stored.event)
            event.results = rows
            replaced = copy(stored)
            replaced.event = event
            applied.append(replaced)
        return applied

    @property
    def edited_keys(self) -> set[tuple[str, str]]:
        return set(self.edits)
