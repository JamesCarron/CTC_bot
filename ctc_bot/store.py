"""Persistence for parsed events.

Raw HTML is archived verbatim under ``data/raw`` and parsed events are written
as JSON under ``data/events``. Keeping both means a parser change can be
replayed over history offline, without re-fetching anything from RaceClocker.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from . import classify as cls
from . import raceclocker as rc

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
RAW_DIR = DATA_DIR / "raw"
EVENTS_DIR = DATA_DIR / "events"


@dataclass
class StoredEvent:
    """A parsed event plus the race type decided for it."""

    event: rc.Event
    race_type: str
    confident: bool
    listing: dict = field(default_factory=dict)

    @property
    def code(self) -> str:
        return self.event.code

    @property
    def title(self) -> str | None:
        return self.event.title

    @property
    def date_text(self) -> str | None:
        return self.event.date_text


def _dirs(data_dir: Path | None = None) -> tuple[Path, Path]:
    base = data_dir or DATA_DIR
    return base / "raw", base / "events"


def save(
    html: str,
    code: str,
    *,
    data_dir: Path | None = None,
    extra: dict | None = None,
) -> StoredEvent:
    """Archive a page, parse it, classify it and write the parsed form.

    ``extra`` carries metadata the admin listing states outright - start type,
    intermediate split count - which is more authoritative than anything
    inferred from the results themselves.
    """
    raw_dir, events_dir = _dirs(data_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    events_dir.mkdir(parents=True, exist_ok=True)

    (raw_dir / f"{code}.html").write_text(html, encoding="utf-8")

    event = rc.parse(html, code)
    classification = cls.classify(event, extra)

    payload = event.to_dict()
    if extra:
        payload["listing"] = extra
    payload["race_type"] = classification.race_type
    payload["confident"] = classification.confident
    payload["classification_notes"] = classification.notes
    (events_dir / f"{code}.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )

    return StoredEvent(
        event, classification.race_type, classification.confident, extra or {}
    )


def ingest(code: str, *, data_dir: Path | None = None) -> StoredEvent:
    """Fetch an event from RaceClocker and store it."""
    return save(rc.fetch(code), code, data_dir=data_dir)


def reclassify_all(*, data_dir: Path | None = None) -> int:
    """Re-parse and re-classify every stored event from its archived HTML.

    This is why raw pages are kept: a change to parsing or classification can
    be replayed across the whole history offline, without re-fetching anything
    from RaceClocker. Listing metadata already captured is preserved.
    """
    raw_dir, events_dir = _dirs(data_dir)
    updated = 0
    for path in sorted(events_dir.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        code = payload["code"]
        raw_path = raw_dir / f"{code}.html"
        if not raw_path.exists():
            continue
        save(
            raw_path.read_text(encoding="utf-8"),
            code,
            data_dir=data_dir,
            extra=payload.get("listing"),
        )
        updated += 1
    return updated


def load_all(*, data_dir: Path | None = None) -> list[StoredEvent]:
    """Load every stored event, newest-looking codes last (stable by filename)."""
    _, events_dir = _dirs(data_dir)
    if not events_dir.exists():
        return []

    stored: list[StoredEvent] = []
    for path in sorted(events_dir.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        event = rc.Event(
            code=payload["code"],
            title=payload.get("title"),
            date_text=payload.get("date_text"),
            distance=payload.get("distance"),
            distance_unit=payload.get("distance_unit"),
            split_names=payload.get("split_names", []),
            categories=payload.get("categories", []),
            results=payload.get("results", []),
        )
        stored.append(
            StoredEvent(
                event,
                payload.get("race_type", cls.UNKNOWN),
                payload.get("confident", False),
                payload.get("listing", {}),
            )
        )
    return stored
