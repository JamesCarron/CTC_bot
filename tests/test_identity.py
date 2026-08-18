"""Tests for claim-based athlete identity.

The scenarios that matter are the ones where guessing would be wrong: two
people sharing a first name, a name learned from a claim reappearing in a later
event, and the same spelling belonging to two different claimed athletes.
"""

from pathlib import Path

import pytest

from ctc_bot import classify as cls
from ctc_bot import identity as idn
from ctc_bot import raceclocker as rc
from ctc_bot import store

FIXTURES = Path(__file__).parent


def make_event(code, rows, *, race_type=cls.TIME_TRIAL, date_text="Tuesday 1 Sep '26, 19:00"):
    """Build a StoredEvent from minimal rows, for scenarios not in the fixtures."""
    results = [
        {
            "RaceID": str(1000 + index),
            "Name": name,
            "Bib": str(index + 1),
            "Rank": str(index + 1),
            "Result": "00:20:00.0",
            "TmResultSec": str(seconds),
            "TmSplit1": "19:00:00",
            "TmSplit1dc": "0",
            "TmSplit5": "19:20:00",
            "TmSplit5dc": "0",
        }
        for index, (name, seconds) in enumerate(rows)
    ]
    event = rc.Event(
        code=code,
        title="Synthetic",
        date_text=date_text,
        distance=13.0,
        distance_unit="km",
        results=results,
    )
    return store.StoredEvent(event, race_type, True)


@pytest.fixture(scope="module")
def real_events():
    stored = []
    for code, race_type in (("7eecd645", cls.TIME_TRIAL), ("dd7293a5", cls.AQUATHON)):
        html = (FIXTURES / f"fixtures_{code}.html").read_text(encoding="utf-8")
        stored.append(store.StoredEvent(rc.parse(html, code), race_type, True))
    return stored


# ---- normalisation -------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [("Dylan ", "dylan"), ("  Kevin   G ", "kevin g"), ("KEVIN", "kevin"), ("", "")],
)
def test_normalise(raw, expected):
    assert idn.normalise(raw) == expected


def test_first_name_prefix_matching_surfaces_both_kevins(real_events):
    found = idn.Registry().search("Kev", real_events)
    assert {c.name for c in found} == {"Kevin G", "Kevin"}


def test_search_spans_events(real_events):
    """John raced both the TT and the aquathon."""
    found = idn.Registry().search("John", real_events)
    assert {c.event for c in found} == {"7eecd645", "dd7293a5"}


def test_search_positions_are_recomputed_not_source_rank(real_events):
    """Kevin G won the TT on time despite carrying source Rank 2."""
    kevin_g = next(c for c in idn.Registry().search("Kevin", real_events) if c.name == "Kevin G")
    assert kevin_g.position == 1


# ---- claiming ------------------------------------------------------------


def test_claim_keeps_two_kevins_apart(real_events):
    registry = idn.Registry()
    candidates = registry.search("Kevin", real_events)
    fast = next(c for c in candidates if c.name == "Kevin G")
    slow = next(c for c in candidates if c.name == "Kevin")

    registry.claim("Kevin Gallagher", [fast])
    registry.claim("Kevin Murphy", [slow])

    assert len(registry.athletes) == 2
    gallagher = registry.find_by_display_name("Kevin Gallagher")
    murphy = registry.find_by_display_name("Kevin Murphy")
    assert gallagher.name_variants == {"kevin g"}
    assert murphy.name_variants == {"kevin"}


def test_claim_round_trips_through_disk(real_events, tmp_path):
    registry = idn.Registry()
    registry.claim("John Doyle", registry.search("John", real_events))
    path = registry.save(tmp_path / "identity.json")

    reloaded = idn.Registry.load(path)
    john = reloaded.find_by_display_name("John Doyle")
    assert len(john.claims) == 2
    assert john.name_variants == {"john"}


def test_claimed_rows_are_marked_on_later_searches(real_events):
    registry = idn.Registry()
    candidates = registry.search("Kevin", real_events)
    registry.claim("Kevin Gallagher", [candidates[0]])

    again = registry.search("Kevin", real_events)
    claimed = [c for c in again if c.claimed_by]
    assert len(claimed) == 1
    assert claimed[0].name == "Kevin G"


def test_reclaiming_a_row_moves_it(real_events):
    """A mistaken selection is corrected by claiming it under the right person."""
    registry = idn.Registry()
    row = registry.search("John", real_events)[:1]
    registry.claim("Wrong Person", row)
    registry.claim("John Doyle", row)

    assert registry.find_by_display_name("Wrong Person").claims == []
    assert len(registry.find_by_display_name("John Doyle").claims) == 1


# ---- resolution ----------------------------------------------------------


def test_claim_teaches_the_name_for_future_events(real_events):
    """The whole point of remembering: a later event resolves with no new input."""
    registry = idn.Registry()
    kevin_g = next(c for c in registry.search("Kevin", real_events) if c.name == "Kevin G")
    registry.claim("Kevin Gallagher", [kevin_g])

    future = make_event("newevent", [("Kevin G", 1180.0), ("Brendan", 1300.0)])
    resolutions = idn.resolve([future], registry)

    kevin_row = resolutions[("newevent", "1000")]
    assert kevin_row.source == idn.INFERRED
    assert kevin_row.display_name == "Kevin Gallagher"
    assert not kevin_row.needs_claim


def test_same_spelling_claimed_by_two_athletes_is_ambiguous():
    """Never guess between two people who both race as "Kevin"."""
    first = make_event("evt_a", [("Kevin", 1200.0)])
    second = make_event("evt_b", [("Kevin", 1400.0)])
    registry = idn.Registry()
    registry.claim("Kevin Gallagher", registry.search("Kevin", [first]))
    registry.claim("Kevin Murphy", registry.search("Kevin", [second]))

    third = make_event("evt_c", [("Kevin", 1300.0)])
    resolution = idn.resolve([third], registry)[("evt_c", "1000")]

    assert resolution.source == idn.AMBIGUOUS
    assert resolution.needs_claim
    assert resolution.athlete_id is None


def test_duplicate_name_within_one_event_is_contested():
    """Two "Kevin"s in one race proves they are different people."""
    event = make_event("evt_dup", [("Kevin", 1200.0), ("Kevin", 1400.0)])
    assert idn.contested_names([event]) == {"kevin"}

    resolutions = idn.resolve([event], idn.Registry())
    assert all(r.source == idn.CONTESTED for r in resolutions.values())
    assert all(r.needs_claim for r in resolutions.values())


def test_unclaimed_unique_names_group_provisionally(real_events):
    """Unclaimed athletes still get a trend, grouped by exact name."""
    resolutions = idn.resolve(real_events, idn.Registry())
    kathleen = [r for r in resolutions.values() if r.display_name == "Kathleen"]
    assert len(kathleen) == 2  # raced both events
    assert {r.athlete_id for r in kathleen} == {"name:kathleen"}
    assert all(r.source == idn.PROVISIONAL for r in kathleen)


def test_claims_take_precedence_over_provisional_grouping(real_events):
    registry = idn.Registry()
    registry.claim("John Doyle", registry.search("John", real_events))
    resolutions = idn.resolve(real_events, registry)

    johns = [r for r in resolutions.values() if r.display_name == "John Doyle"]
    assert len(johns) == 2
    assert all(r.source == idn.CLAIMED for r in johns)


def test_every_row_is_resolved_to_something(real_events):
    """Nobody silently vanishes from their own trend line."""
    resolutions = idn.resolve(real_events, idn.Registry())
    total = sum(len(s.event.results) for s in real_events)
    assert len(resolutions) == total


# ---- formatting ----------------------------------------------------------


@pytest.mark.parametrize(
    "seconds, expected",
    [(1274.0, "21:14.0"), (1212.8, "20:12.8"), (3661.5, "1:01:01.5"), (None, "-")],
)
def test_format_time(seconds, expected):
    assert idn.format_time(seconds) == expected
