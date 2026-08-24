"""Propose spellings that are probably the same person.

Results are grouped only by the name typed on the entry sheet, so one rider is
scattered across every spelling the timekeeper ever used - *Brendan O'Sullivan*,
*Brendan O Sullivan* and *Brendan OSullivan* are three separate people as far as
the site is concerned, each with a short broken history. Somebody claiming their
own results fixes this one person at a time; across 486 distinct aquathon names
that is never going to finish.

This module does the reading and hands an admin a list to tick, which is the
only safe division of labour: a machine is good at noticing that two spellings
look alike and hopeless at knowing whether two people share a name. **Nothing
here ever merges anything.** It proposes; a person confirms.

Three rules keep the proposals honest:

* **The first name must match exactly.** Surname spellings wander; first names
  are typed the same way nearly every time, and matching loosely on both at once
  is how *Colin Feeley* ends up joined to *Colm Feely*.
* **Two spellings that appear in the same race are never proposed.** Nobody
  races twice, so co-occurrence is proof of two people rather than evidence of
  one. This rejects 12 otherwise-plausible pairs in the club's history.
* **A bare first name is a weak signal, and only offered when it is
  unambiguous.** *Adrian* alongside *Adrian Quinn* is a fair guess; *Adrian*
  alongside both *Adrian Quinn* and *Adrian Wall* is a coin toss, so neither is
  offered.
"""

from __future__ import annotations

import difflib
import unicodedata
from dataclasses import dataclass, field

from . import identity as idn

# How alike two surnames must look before spelling drift is the likelier
# explanation. 0.85 accepts "feeley"/"feely" and "sheahan"/"sheehan" while
# rejecting "quinn"/"wall"; it also catches the handful of names mangled into
# mojibake by an encoding slip somewhere upstream.
SURNAME_RATIO = 0.85

# A surname this short next to a longer one reads as an initial: "James C" is
# James Carron shortened, not a surname in its own right.
INITIAL_LENGTH = 2

STRONG = "strong"
WEAK = "weak"

# Ordered by how much they are worth believing; a group is labelled with its
# weakest link.
SPACING = "spacing or apostrophe"
INITIAL = "shortened to an initial"
SPELLING = "spelling drift"
BARE = "first name only"

_STRENGTH = {SPACING: STRONG, INITIAL: STRONG, SPELLING: STRONG, BARE: WEAK}


@dataclass
class Variant:
    """One spelling, and what it currently amounts to on its own."""

    name: str  # the spelling as printed, in its commonest form
    normalised: str
    races: int
    owner_id: str | None = None
    owner_name: str | None = None


@dataclass
class Suggestion:
    """A set of spellings proposed as one person, for a human to confirm."""

    key: str
    display_name: str
    variants: list[Variant]
    reasons: list[str]
    confidence: str
    races: int
    race_types: list[str] = field(default_factory=list)
    target_id: str | None = None
    """An existing athlete to merge into, when exactly one of the variants is
    already claimed. Merging into them keeps that person's id and their history
    intact rather than starting a third identity beside the two being joined."""

    joins_claimed: bool = False
    """True when the variants already belong to *different* confirmed people.
    Confirming would move one person's claimed results onto another, so the
    interface has to say so rather than treat it as tidying up."""


# ---- the reading ---------------------------------------------------------


# Typographic apostrophes and the decomposed forms of accented letters are the
# two ways an identical name compares unequal. Both are real in the club's data:
# 21 names carry a curly apostrophe and 12 of those also exist with a straight
# one, while three names arrive with the fada as a combining accent.
_APOSTROPHES = str.maketrans({"’": "'", "ʼ": "'", "‘": "'", "`": "'"})


def _fold(name: str) -> str:
    """Compare-only form: one apostrophe, one way of writing an accent.

    Deliberately *not* pushed down into :func:`identity.normalise`. That
    function is the key every claim is recorded against, so widening it would
    silently re-group athletes without anybody confirming anything - which is
    exactly the decision this module refuses to make on its own.
    """
    return unicodedata.normalize("NFC", name).translate(_APOSTROPHES)


def _surname(normalised: str) -> str:
    return " ".join(normalised.split(" ")[1:])


def relation(a: str, b: str) -> str | None:
    """Why two normalised names might be one person, or ``None``.

    Both names are assumed to share a first name already.
    """
    tokens_a, tokens_b = a.split(" "), b.split(" ")
    if len(tokens_a) == 1 or len(tokens_b) == 1:
        # One is a bare first name. Whether that is usable at all depends on
        # what else shares the first name, which is decided by the caller.
        return BARE if a != b else None

    left, right = _fold(_surname(a)), _fold(_surname(b))
    if left == right:
        # The same surname once apostrophes and accents are read the same way.
        # Only worth proposing when the raw spellings actually differ.
        return SPACING if _surname(a) != _surname(b) else None

    # Punctuation and spacing inside a surname - "o'driscoll", "o driscoll",
    # "odriscoll" - is the single commonest way one person splits in two.
    def bare(text: str) -> str:
        return text.replace(" ", "").replace("'", "").replace("-", "")

    if bare(left) == bare(right):
        return SPACING

    for short, long in ((left, right), (right, left)):
        if len(short) <= INITIAL_LENGTH and long.startswith(short):
            return INITIAL

    if difflib.SequenceMatcher(None, left, right).ratio() >= SURNAME_RATIO:
        return SPELLING
    return None


class _Groups:
    """Union-find, so three spellings of one name come out as one group.

    Pairwise output would offer the same person three times over and let an
    admin confirm two of the three, leaving the split half-fixed.
    """

    def __init__(self):
        self.parent: dict[str, str] = {}
        self.why: dict[tuple[str, str], str] = {}

    def find(self, item: str) -> str:
        self.parent.setdefault(item, item)
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, a: str, b: str, reason: str) -> None:
        self.why[(a, b)] = reason
        root_a, root_b = self.find(a), self.find(b)
        if root_a != root_b:
            self.parent[root_b] = root_a

    def sets(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for item in self.parent:
            out.setdefault(self.find(item), []).append(item)
        return {root: sorted(names) for root, names in out.items() if len(names) > 1}


def _is_short(name: str) -> bool:
    """A bare first name, or a surname too short to identify anybody.

    These are the ambiguous glue. Left to chain freely they join *John O* to
    O'Connell, O'Driscoll and O'Shaughnessy at once, and union-find then reports
    three different men as one person with 103 races.
    """
    surname = _surname(name)
    return not surname or len(surname) <= INITIAL_LENGTH


def _group_bucket(bucket, events, groups: "_Groups", *, include_weak: bool) -> None:
    """Group one first name's spellings, in two passes.

    Full names are joined to each other first, so the substantive spellings
    decide how many people there are. Only then is each abbreviation offered a
    home, and only when exactly one group could take it.
    """
    full = [name for name in bucket if not _is_short(name)]
    short = [name for name in bucket if _is_short(name)]

    def compatible(a: str, b: str) -> str | None:
        if events[a] & events[b]:
            return None  # nobody races twice: this is two people
        return relation(a, b)

    for index, left in enumerate(full):
        for right in full[index + 1 :]:
            reason = compatible(left, right)
            if reason:
                groups.union(left, right, reason)

    # Every full name is its own group even when nothing joined it, so an
    # abbreviation can still be told how many candidates it has.
    homes: dict[str, list[str]] = {}
    for name in full:
        homes.setdefault(groups.find(name) if name in groups.parent else name, []).append(name)

    for name in short:
        matches = {}
        for root, members in homes.items():
            for member in members:
                reason = compatible(name, member)
                if reason:
                    matches[root] = (member, reason)
                    break
        if len(matches) != 1:
            continue  # nothing to attach to, or a coin toss between people
        member, reason = next(iter(matches.values()))
        if reason is BARE and not include_weak:
            continue
        groups.union(member, name, reason)


def _index(stored_events, registry: idn.Registry):
    """Everything the proposals need, in one pass over the results."""
    spellings: dict[str, dict[str, int]] = {}  # normalised -> printed -> count
    events: dict[str, set[str]] = {}
    race_types: dict[str, set[str]] = {}
    owners: dict[str, set[str]] = {}
    rows: dict[str, list[tuple[str, str, str]]] = {}  # normalised -> (event, race_id, printed)

    for stored in stored_events:
        for row in stored.event.results:
            printed = (row.get("Name") or "").strip()
            if not printed or idn.is_placeholder(printed):
                continue
            key = idn.normalise(printed)
            race_id = str(row.get("RaceID", ""))

            spellings.setdefault(key, {})
            spellings[key][printed] = spellings[key].get(printed, 0) + 1
            events.setdefault(key, set()).add(stored.code)
            race_types.setdefault(key, set()).add(stored.race_type)
            rows.setdefault(key, []).append((stored.code, race_id, printed))

            owner = registry.claim_owner(stored.code, race_id)
            if owner:
                owners.setdefault(key, set()).add(owner)

    return spellings, events, race_types, owners, rows


def suggest(stored_events, registry: idn.Registry, *, include_weak: bool = True) -> list[Suggestion]:
    """Spellings that are probably one person, strongest first.

    Never merges anything and never touches the registry - the caller shows
    these to somebody who can say yes.
    """
    spellings, events, race_types, owners, rows = _index(stored_events, registry)
    names = sorted(spellings)

    buckets: dict[str, list[str]] = {}
    for name in names:
        buckets.setdefault(name.split(" ")[0], []).append(name)

    groups = _Groups()
    for bucket in buckets.values():
        _group_bucket(bucket, events, groups, include_weak=include_weak)

    suggestions = []
    for members in groups.sets().values():
        reasons = sorted(
            {
                reason
                for (a, b), reason in groups.why.items()
                if a in members and b in members
            }
        )
        if not reasons:
            continue

        owning = set().union(*(owners.get(name, set()) for name in members))
        # Already one person: the claims have joined these spellings, and the
        # site is showing them together. Nothing left to propose.
        if len(owning) == 1 and all(owners.get(name) == owning for name in members):
            continue

        variants = [
            Variant(
                name=max(spellings[name].items(), key=lambda kv: (kv[1], kv[0]))[0],
                normalised=name,
                races=sum(spellings[name].values()),
                owner_id=next(iter(owners.get(name, ())), None),
                owner_name=_owner_name(registry, owners.get(name)),
            )
            for name in members
        ]
        # The fullest spelling somebody actually used, preferring the one most
        # often typed - never a name invented by joining the others together.
        best = max(
            variants,
            key=lambda v: (len(v.normalised.split(" ")) > 1, v.races, len(v.name)),
        )

        target = next(iter(owning)) if len(owning) == 1 else None
        existing = registry.athletes[target].display_name if target in registry.athletes else None
        suggestions.append(
            Suggestion(
                key="|".join(sorted(members)),
                # Merging into somebody keeps the name they are already listed
                # under; nothing about their identity is up for revision here.
                display_name=existing or best.name,
                variants=sorted(variants, key=lambda v: (-v.races, v.normalised)),
                reasons=reasons,
                confidence=min((_STRENGTH[r] for r in reasons), key=lambda s: s != WEAK),
                races=sum(v.races for v in variants),
                race_types=sorted(set().union(*(race_types[name] for name in members))),
                target_id=target,
                joins_claimed=len(owning) > 1,
            )
        )

    suggestions = [s for s in suggestions if s.key not in registry.dismissed_merges]
    suggestions.sort(key=lambda s: (s.confidence != STRONG, -s.races, s.display_name))
    return suggestions


def find(key: str, stored_events, registry: idn.Registry) -> Suggestion | None:
    """Re-derive one suggestion by key.

    Suggestions are computed rather than stored, so applying one means working
    it out again from the current data. That is deliberate: a key that no longer
    describes a real group - because somebody claimed those rows in the
    meantime - simply stops existing, instead of applying a merge based on what
    the page said an hour ago.
    """
    return next((s for s in suggest(stored_events, registry) if s.key == key), None)


def apply(suggestion: Suggestion, stored_events, registry: idn.Registry, *, display_name: str = "") -> int:
    """Claim every row in a suggestion under one athlete. Returns rows moved.

    Uses the same claim machinery a person uses when confirming their own
    results, so a merge is exactly a batch of ordinary claims - and is undone
    the same way, one **Not mine** at a time.
    """
    candidates = candidates_for(suggestion, stored_events, registry)
    if not candidates:
        return 0
    registry.claim(
        (display_name or suggestion.display_name).strip(),
        candidates,
        athlete_id=suggestion.target_id,
    )
    return len(candidates)


def _owner_name(registry: idn.Registry, owner_ids: set[str] | None) -> str | None:
    if not owner_ids:
        return None
    athlete = registry.athletes.get(next(iter(owner_ids)))
    return athlete.display_name if athlete else None


def candidates_for(suggestion: Suggestion, stored_events, registry: idn.Registry) -> list[idn.Candidate]:
    """Every result row covered by a suggestion, ready to be claimed."""
    wanted = {variant.normalised for variant in suggestion.variants}
    found = []
    for stored in stored_events:
        for row in stored.event.results:
            printed = (row.get("Name") or "").strip()
            if idn.normalise(printed) not in wanted:
                continue
            found.append(
                idn.Candidate(
                    event=stored.code,
                    event_title=stored.title,
                    date_text=stored.date_text,
                    race_type=stored.race_type,
                    race_id=str(row.get("RaceID", "")),
                    name=printed,
                    bib=str(row.get("Bib", "")),
                    seconds=_seconds(row),
                    position=row.get("Position"),
                    claimed_by=registry.claim_owner(stored.code, str(row.get("RaceID", ""))),
                )
            )
    return found


def _seconds(row: dict) -> float | None:
    try:
        return float(row.get("TmResultSec"))
    except (TypeError, ValueError):
        return None
