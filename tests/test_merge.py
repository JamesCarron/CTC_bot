"""Tests for merge suggestions.

Weighted towards the *false* merges, because those are the expensive mistake.
A missed suggestion leaves a name split, which is the state the site is already
in; a wrong one silently attributes one person's races to another, and looks
authoritative while doing it.
"""

from datetime import date, timedelta

from ctc_bot import classify as cls
from ctc_bot import identity as idn
from ctc_bot import merge
from ctc_bot import raceclocker as rc
from ctc_bot import store


def make_event(code, names, *, race_type=cls.TIME_TRIAL, day=1):
    when = date(2026, 5, 1) + timedelta(days=day)
    results = [
        {
            "RaceID": str(1000 + index),
            "Name": name,
            "Bib": str(index + 1),
            "Rank": str(index + 1),
            "Result": "00:00:00.0",
            "TmResultSec": str(1500 + index * 20),
            "TmSplit1": "19:00:00",
            "TmSplit1dc": "0",
            "TmSplit5": "19:25:00",
            "TmSplit5dc": "0",
        }
        for index, name in enumerate(names)
    ]
    event = rc.Event(
        code=code,
        title="Synthetic TT",
        date_text=f"{when:%A} {when.day} {when:%b} '{when:%y}, 19:00",
        distance=13.0,
        distance_unit="km",
        results=results,
    )
    return store.StoredEvent(event, race_type, True, {"listed_distance_km": 13.0})


def names_in(suggestions):
    return {frozenset(v.normalised for v in s.variants) for s in suggestions}


def suggest(events, registry=None):
    return merge.suggest(events, registry or idn.Registry())


# ---- what it should catch ------------------------------------------------


def test_apostrophes_and_spacing_are_one_person():
    """The commonest way a single rider splits in two."""
    events = [
        make_event("e1", ["Brendan O'Sullivan"], day=1),
        make_event("e2", ["Brendan O Sullivan"], day=8),
        make_event("e3", ["Brendan OSullivan"], day=15),
    ]
    found = suggest(events)
    assert len(found) == 1
    assert len(found[0].variants) == 3
    assert found[0].confidence == merge.STRONG


def test_an_initial_joins_its_full_name():
    events = [make_event("e1", ["Kieran Kennedy"], day=1), make_event("e2", ["Kieran K"], day=8)]
    assert names_in(suggest(events)) == {frozenset({"kieran kennedy", "kieran k"})}


def test_spelling_drift_is_caught():
    events = [make_event("e1", ["Colin Feeley"], day=1), make_event("e2", ["Colin Feely"], day=8)]
    assert len(suggest(events)) == 1


# ---- what it must NOT catch ----------------------------------------------


def test_two_people_in_the_same_race_are_never_proposed():
    """Nobody races twice, so appearing together is proof of two people."""
    events = [make_event("e1", ["Sean Murphy", "Sean M"], day=1)]
    assert suggest(events) == []


def test_an_ambiguous_initial_is_left_alone():
    """`Peter M` could be Peter Meaney or Peter Martin.

    A coin toss is worse than leaving the name split, because it looks decided.
    """
    events = [
        make_event("e1", ["Peter Meaney"], day=1),
        make_event("e2", ["Peter Martin"], day=8),
        make_event("e3", ["Peter M"], day=15),
    ]
    assert suggest(events) == []


def test_a_short_surname_does_not_chain_different_families():
    """The bug this guards.

    `John O` matches O'Connell, O'Driscoll and O'Shaughnessy alike, and
    union-find then reports three different men as one person with 103 races.
    """
    events = [
        make_event("e1", ["John O'Connell"], day=1),
        make_event("e2", ["John O'Driscoll"], day=8),
        make_event("e3", ["John O'Shaughnessy"], day=15),
        make_event("e4", ["John O"], day=22),
    ]
    for group in names_in(suggest(events)):
        full = {name for name in group if len(name.split(" ", 1)[1]) > merge.INITIAL_LENGTH}
        assert len(full) <= 1, f"two different families joined: {group}"


def test_different_surnames_are_not_joined():
    events = [make_event("e1", ["Adrian Quinn"], day=1), make_event("e2", ["Adrian Wall"], day=8)]
    assert suggest(events) == []


def test_different_first_names_are_never_joined():
    """Surnames wander; first names do not.

    Matching loosely on both at once is how Colin Feeley reaches Colm Feely.
    """
    events = [make_event("e1", ["Colin Feeley"], day=1), make_event("e2", ["Colm Feely"], day=8)]
    assert suggest(events) == []


def test_placeholders_are_ignored():
    events = [make_event("e1", ["unknown"], day=1), make_event("e2", ["unknown 2"], day=8)]
    assert suggest(events) == []


# ---- bare first names ----------------------------------------------------


def test_a_bare_first_name_is_offered_only_when_unambiguous():
    events = [make_event("e1", ["Bryce Whibley"], day=1), make_event("e2", ["Bryce"], day=8)]
    found = suggest(events)
    assert len(found) == 1
    assert found[0].confidence == merge.WEAK


def test_a_bare_first_name_with_two_candidates_is_dropped():
    events = [
        make_event("e1", ["Adrian Quinn"], day=1),
        make_event("e2", ["Adrian Wall"], day=8),
        make_event("e3", ["Adrian"], day=15),
    ]
    assert suggest(events) == []


def test_weak_suggestions_can_be_switched_off():
    events = [make_event("e1", ["Bryce Whibley"], day=1), make_event("e2", ["Bryce"], day=8)]
    assert merge.suggest(events, idn.Registry(), include_weak=False) == []


# ---- naming and application ----------------------------------------------


def test_the_proposed_name_is_one_somebody_actually_typed():
    events = [
        make_event("e1", ["Tim O'Sullivan"], day=1),
        make_event("e2", ["Tim O'Sullivan"], day=8),
        make_event("e3", ["Tim OSullivan"], day=15),
    ]
    assert suggest(events)[0].display_name == "Tim O'Sullivan"


def test_a_curly_apostrophe_is_the_same_name():
    """21 club names carry a typographic apostrophe and 12 of those also exist
    with a straight one. Compared literally they are different people."""
    events = [
        make_event("e1", ["John O’Regan"], day=1),
        make_event("e2", ["John O'Regan"], day=8),
    ]
    found = suggest(events)
    assert len(found) == 1
    assert found[0].reasons == [merge.SPACING]


def test_an_accent_written_two_ways_is_the_same_name():
    """Three names arrive with the fada as a combining accent rather than a
    single character. The two spellings look identical and compare unequal."""
    events = [
        make_event("e1", ["Aoife Ní Mhurchu"], day=1),   # decomposed
        make_event("e2", ["Aoife Ní Mhurchu"], day=8),    # composed
    ]
    assert len(suggest(events)) == 1


def test_applying_claims_every_row():
    events = [make_event("e1", ["Kieran Kennedy"], day=1), make_event("e2", ["Kieran K"], day=8)]
    registry = idn.Registry()
    moved = merge.apply(merge.suggest(events, registry)[0], events, registry)

    assert moved == 2
    athlete = registry.find_by_display_name("Kieran Kennedy")
    assert athlete is not None
    assert len(athlete.claims) == 2
    # And the suggestion is gone, because there is nothing left to propose.
    assert merge.suggest(events, registry) == []


def test_applying_merges_into_an_existing_athlete():
    """A confirmed identity absorbs the variants.

    Otherwise a third person appears beside the two being joined.
    """
    events = [make_event("e1", ["Kieran Kennedy"], day=1), make_event("e2", ["Kieran K"], day=8)]
    registry = idn.Registry()
    first = merge.candidates_for(merge.suggest(events, registry)[0], events, registry)[0]
    registry.claim("Kieran Kennedy", [first])
    before = set(registry.athletes)

    suggestion = merge.suggest(events, registry)[0]
    assert suggestion.target_id in before
    merge.apply(suggestion, events, registry)
    assert set(registry.athletes) == before, "no new identity created"


def test_a_dismissed_suggestion_is_not_offered_again():
    events = [make_event("e1", ["Kieran Kennedy"], day=1), make_event("e2", ["Kieran K"], day=8)]
    registry = idn.Registry()
    registry.dismissed_merges.add(merge.suggest(events, registry)[0].key)
    assert merge.suggest(events, registry) == []


def test_dismissals_survive_a_save(tmp_path):
    path = tmp_path / "identity.json"
    registry = idn.Registry()
    registry.dismissed_merges.add("kieran k|kieran kennedy")
    registry.save(path)
    assert idn.Registry.load(path).dismissed_merges == {"kieran k|kieran kennedy"}
