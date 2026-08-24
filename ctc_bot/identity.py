"""Athlete identity: search by first name, claim your own results, remembered.

RaceClocker publishes no usable identifying data. Across both sample events
``Gender`` is "Male" for every athlete (including Kathleen, Fiona, Dee, Maura,
Lorraine, Keelin and Sinead), while ``Age``, ``Club``, ``Cat`` and ``Wave`` are
empty or defaults. The name string is all there is, and bib numbers are
reassigned every event.

So identity is **claimed, not inferred**. An athlete types their first name,
sees every matching result across all events, and ticks the ones that are
theirs. The claim is recorded against a specific result row
(``event code + RaceID``) rather than against a name string - a name is
ambiguous, a row is not. Name variants are then *derived* from claims, which is
what lets `Kevin` and `Kevin G` be two different people without either of them
corrupting the other's history.

Later events are resolved automatically from what previous claims taught us,
and anything genuinely ambiguous is surfaced rather than guessed.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

IDENTITY_PATH = Path(__file__).resolve().parent.parent / "data" / "identity.json"

_WHITESPACE_RE = re.compile(r"\s+")

# How a row was attributed to an athlete.
CLAIMED = "claimed"  # the athlete ticked this exact row
INFERRED = "inferred"  # name variant learned from that athlete's own claims
PROVISIONAL = "provisional"  # nobody has claimed it; grouped by exact name
AMBIGUOUS = "ambiguous"  # name maps to more than one claimed athlete
CONTESTED = "contested"  # two people share this name within a single event
PLACEHOLDER = "placeholder"  # not a person's name at all
OPTED_OUT = "opted_out"  # a real person who asked not to be listed

# Only a claim, or a spelling learned from one, makes an identity trustworthy.
VERIFIED_SOURCES = frozenset({CLAIMED, INFERRED})

# Below this many races a fitted trend line says more about noise than form.
MIN_RACES_FOR_TREND = 3

# Entries that are not people. 91 rows across the club's history are recorded
# as "unknown" (62), "name" (17), a bare bib number, or "test". They still
# raced, so they keep counting towards field size for z-scores and percentiles,
# but they must never become an athlete or a trend line.
_PLACEHOLDER_RE = re.compile(
    r"^(unknown|name|test|tbc|n/?a|none|guest|anon\w*|athlete|placeholder|x+|\?+|-+|\.+|\d+)$",
    re.I,
)


def is_placeholder(name: str) -> bool:
    """True if this entry is a stand-in rather than a person."""
    normalised = normalise(name)
    return not normalised or bool(_PLACEHOLDER_RE.match(normalised))


def normalise(name: str) -> str:
    """Casefold and collapse whitespace for matching only.

    Display always uses the original spelling; ``"Dylan "`` in the sample data
    carries a trailing space that must not leak into comparisons.
    """
    return _WHITESPACE_RE.sub(" ", (name or "").strip()).casefold()


def first_name(name: str) -> str:
    normalised = normalise(name)
    return normalised.split(" ")[0] if normalised else ""


def format_time(seconds: float | None) -> str:
    """Render seconds as ``h:mm:ss.s`` / ``mm:ss.s``."""
    if seconds is None:
        return "-"
    hours, remainder = divmod(float(seconds), 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{int(hours)}:{int(minutes):02d}:{secs:04.1f}"
    return f"{int(minutes)}:{secs:04.1f}"


@dataclass
class Claim:
    """One result row an athlete has confirmed is theirs."""

    event: str
    race_id: str
    name: str  # name exactly as printed on that event

    @property
    def key(self) -> tuple[str, str]:
        return (self.event, self.race_id)


@dataclass
class Athlete:
    id: str
    display_name: str
    claims: list[Claim] = field(default_factory=list)

    @property
    def name_variants(self) -> set[str]:
        """Every spelling this athlete has appeared under, derived from claims."""
        return {normalise(claim.name) for claim in self.claims}


@dataclass
class Candidate:
    """A result row offered to someone searching for their own times."""

    event: str
    event_title: str | None
    date_text: str | None
    race_type: str
    race_id: str
    name: str
    bib: str
    seconds: float | None
    position: int | None
    claimed_by: str | None = None  # athlete id, if already claimed

    @property
    def finished(self) -> bool:
        """A zero result means entered but never timed - a DNS or a spare entry."""
        return bool(self.seconds and self.seconds > 0)

    @property
    def time(self) -> str:
        return format_time(self.seconds) if self.finished else "no time"

    def describe(self) -> str:
        held = f"  [already claimed by {self.claimed_by}]" if self.claimed_by else ""
        position = f"#{self.position}" if self.position else "  "
        return (
            f"{self.date_text or self.event:<26} {self.event_title or '':<22} "
            f"{self.name!r:<12} bib {self.bib:<3} {position:>4} {self.time:>10}{held}"
        )


@dataclass
class Resolution:
    """How one result row was attributed."""

    athlete_id: str | None
    display_name: str
    source: str

    @property
    def needs_claim(self) -> bool:
        return self.source in {AMBIGUOUS, CONTESTED}

    @property
    def verified(self) -> bool:
        """Whether a person has confirmed this identity.

        A provisional group is only "everyone who typed this exact name", which
        over seven years may well be more than one person - five of the club's
        most active entries are bare first names. Such trends are shown, but
        marked unverified rather than presented as fact.
        """
        return self.source in VERIFIED_SOURCES

    @property
    def is_athlete(self) -> bool:
        """Whether this row belongs to a person the site may show.

        Someone who has opted out is excluded here but deliberately still
        counted in the field a race is measured against - they really did race,
        and removing them would quietly change everyone else's z-score and
        finishing position.
        """
        return self.source not in {PLACEHOLDER, OPTED_OUT}

    @property
    def may_be_several_people(self) -> bool:
        """Whether this group is known to possibly mix more than one person.

        Contested and ambiguous groups are still shown - dropping them would
        silently erase real results, and one of the club's most active entries
        (17 races) is contested. They are surfaced with a warning instead.
        """
        return self.source in {CONTESTED, AMBIGUOUS}


class Registry:
    """The remembered set of athletes and their claimed rows."""

    def __init__(
        self,
        athletes: dict[str, Athlete] | None = None,
        opted_out: dict[str, dict] | None = None,
        dismissed_merges: list[str] | None = None,
    ):
        self.athletes: dict[str, Athlete] = athletes or {}
        # athlete id -> {"name": ..., "at": ...}. The name is kept so an admin
        # can find the entry to reverse it; nothing about them is shown.
        self.opted_out: dict[str, dict] = opted_out or {}
        # Merge suggestions somebody looked at and rejected. Kept so a wrong
        # guess is refused once rather than every time the page is rebuilt -
        # a suggestion list that keeps re-offering what you just turned down
        # is one nobody reads twice.
        self.dismissed_merges: set[str] = set(dismissed_merges or [])

    # ---- persistence -------------------------------------------------

    @classmethod
    def load(cls, path: Path | None = None) -> "Registry":
        identity_path = path or IDENTITY_PATH
        if not identity_path.exists():
            return cls()
        payload = json.loads(identity_path.read_text(encoding="utf-8"))
        athletes = {
            athlete_id: Athlete(
                id=athlete_id,
                display_name=spec["display_name"],
                claims=[Claim(**claim) for claim in spec.get("claims", [])],
            )
            for athlete_id, spec in payload.get("athletes", {}).items()
        }
        return cls(
            athletes,
            payload.get("opted_out", {}),
            payload.get("dismissed_merges", []),
        )

    def save(self, path: Path | None = None) -> Path:
        identity_path = path or IDENTITY_PATH
        identity_path.parent.mkdir(parents=True, exist_ok=True)
        identity_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "opted_out": self.opted_out,
                    "dismissed_merges": sorted(self.dismissed_merges),
                    "athletes": {
                        athlete_id: {
                            "display_name": athlete.display_name,
                            "claims": [asdict(claim) for claim in athlete.claims],
                        }
                        for athlete_id, athlete in self.athletes.items()
                    },
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return identity_path

    # ---- lookups -----------------------------------------------------

    def claim_owner(self, event: str, race_id: str) -> str | None:
        for athlete in self.athletes.values():
            for claim in athlete.claims:
                if claim.key == (event, str(race_id)):
                    return athlete.id
        return None

    def athletes_using(self, name: str) -> list[Athlete]:
        """Athletes who have claimed at least one row under this exact spelling."""
        wanted = normalise(name)
        return [a for a in self.athletes.values() if wanted in a.name_variants]

    def find_by_display_name(self, display_name: str) -> Athlete | None:
        wanted = normalise(display_name)
        for athlete in self.athletes.values():
            if normalise(athlete.display_name) == wanted:
                return athlete
        return None

    # ---- the claim flow ----------------------------------------------

    def search(self, query: str, stored_events, *, include_claimed: bool = True) -> list[Candidate]:
        """Every result row whose first name matches ``query``.

        Matches on the first name token by prefix, so "Kev" surfaces both
        `Kevin` and `Kevin G` and lets the athlete pick which rows are theirs.
        """
        from . import raceclocker as rc

        wanted = normalise(query)
        if not wanted:
            return []

        candidates: list[Candidate] = []
        for stored in stored_events:
            positions = {
                row["RaceID"]: row["Position"] for row in rc.ranked(stored.event.results)
            }
            for row in stored.event.results:
                if not first_name(row.get("Name", "")).startswith(wanted):
                    continue
                race_id = str(row.get("RaceID", ""))
                owner = self.claim_owner(stored.code, race_id)
                if owner and not include_claimed:
                    continue
                try:
                    seconds = float(row.get("TmResultSec"))
                except (TypeError, ValueError):
                    seconds = None
                candidates.append(
                    Candidate(
                        event=stored.code,
                        event_title=stored.title,
                        date_text=stored.date_text,
                        race_type=stored.race_type,
                        race_id=race_id,
                        name=row.get("Name", ""),
                        bib=str(row.get("Bib", "")),
                        seconds=seconds,
                        position=positions.get(row["RaceID"]),
                        claimed_by=owner,
                    )
                )

        candidates.sort(key=lambda c: (c.date_text or "", c.event, c.seconds or 0))
        return candidates

    def claim(self, display_name: str, candidates: list[Candidate], *, athlete_id: str | None = None) -> Athlete:
        """Record the selected rows as belonging to one athlete.

        Re-claiming a row moves it: the most recent claim wins, so a mistaken
        selection can be corrected by claiming it again under the right person.
        """
        if athlete_id and athlete_id in self.athletes:
            athlete = self.athletes[athlete_id]
        else:
            athlete = self.find_by_display_name(display_name)
        if athlete is None:
            athlete = Athlete(id=f"ath_{uuid.uuid4().hex[:8]}", display_name=display_name.strip())
            self.athletes[athlete.id] = athlete

        for candidate in candidates:
            key = (candidate.event, candidate.race_id)
            for other in self.athletes.values():
                other.claims = [claim for claim in other.claims if claim.key != key]
            athlete.claims.append(
                Claim(event=candidate.event, race_id=candidate.race_id, name=candidate.name)
            )
        return athlete

    # ---- opting out --------------------------------------------------

    def opt_out(self, athlete_id: str, display_name: str) -> None:
        """Record that this person does not want to appear on the site."""
        from datetime import datetime

        self.opted_out[athlete_id] = {
            "name": display_name,
            "at": datetime.now().isoformat(timespec="seconds"),
        }

    def opt_in(self, athlete_id: str) -> bool:
        """Undo an opt-out. Returns True if there was one."""
        return self.opted_out.pop(athlete_id, None) is not None

    def has_opted_out(self, athlete_id: str | None) -> bool:
        return bool(athlete_id) and athlete_id in self.opted_out

    def release(self, athlete_id: str, event: str, race_id: str) -> None:
        athlete = self.athletes.get(athlete_id)
        if athlete:
            athlete.claims = [c for c in athlete.claims if c.key != (event, str(race_id))]


def contested_names(stored_events) -> set[str]:
    """Names that cannot safely be auto-grouped.

    A name appearing twice *within a single event* is either two people sharing
    it or one person entered twice - the club's history contains both, often
    with one of the pair carrying no time at all. Neither reading can be
    assumed, so such names are flagged for a claim rather than merged.
    """
    contested: set[str] = set()
    for stored in stored_events:
        seen: set[str] = set()
        for row in stored.event.results:
            name = normalise(row.get("Name", ""))
            if not name:
                continue
            if name in seen:
                contested.add(name)
            seen.add(name)
    return contested


def resolve(stored_events, registry: Registry) -> dict[tuple[str, str], Resolution]:
    """Attribute every result row across all events to an athlete.

    Precedence:

    0. **placeholder** - not a person ("unknown", "name", a bare bib number).
    1. **claimed** - the athlete ticked this row.
    2. **inferred** - the spelling was learned from exactly one athlete's claims.
    3. **ambiguous** - the spelling belongs to two or more claimed athletes.
    4. **contested** - two people share the name inside one event.
    5. **provisional** - unclaimed, but the spelling is unique, so group by name.
    """
    contested = contested_names(stored_events)
    contested -= {name for name in contested if _PLACEHOLDER_RE.match(name)}
    resolutions: dict[tuple[str, str], Resolution] = {}

    for stored in stored_events:
        for row in stored.event.results:
            race_id = str(row.get("RaceID", ""))
            key = (stored.code, race_id)
            raw_name = row.get("Name", "")
            name = normalise(raw_name)
            display = (raw_name or "").strip()

            if is_placeholder(raw_name):
                resolutions[key] = Resolution(None, display or "(unnamed)", PLACEHOLDER)
                continue

            owner = registry.claim_owner(stored.code, race_id)
            if owner:
                if registry.has_opted_out(owner):
                    resolutions[key] = Resolution(None, "", OPTED_OUT)
                    continue
                resolutions[key] = Resolution(
                    owner, registry.athletes[owner].display_name, CLAIMED
                )
                continue

            # An unclaimed group can opt out too - somebody may want off the
            # site without first proving which results are theirs.
            if registry.has_opted_out(f"name:{name}"):
                resolutions[key] = Resolution(None, "", OPTED_OUT)
                continue

            owners = registry.athletes_using(name)
            if len(owners) == 1:
                if registry.has_opted_out(owners[0].id):
                    resolutions[key] = Resolution(None, "", OPTED_OUT)
                    continue
                resolutions[key] = Resolution(
                    owners[0].id, owners[0].display_name, INFERRED
                )
            elif len(owners) > 1:
                # Grouped so the results stay visible, but flagged: this bucket
                # may hold more than one person until someone claims their rows.
                resolutions[key] = Resolution(f"ambiguous:{name}", display, AMBIGUOUS)
            elif name in contested:
                resolutions[key] = Resolution(f"contested:{name}", display, CONTESTED)
            else:
                resolutions[key] = Resolution(f"name:{name}", display, PROVISIONAL)

    return resolutions
