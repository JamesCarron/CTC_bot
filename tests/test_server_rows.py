"""Tests for adding and releasing individual results.

Claiming a whole group is the common case, but the group is only ever a guess
from a name. These two operations are how a person corrects it: pull in a
result recorded under a spelling nobody would look for, and push back one that
was never theirs.
"""

import pytest

from ctc_bot import identity as idn
from ctc_bot import server
from tests.test_metrics import make_event


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A small stored history plus an isolated registry on disk."""
    events = [
        make_event("e1", [("James Carron", 1500.0), ("Ann", 1550.0), ("Bea", 1600.0),
                          ("Cid", 1650.0), ("Dee", 1700.0)],
                   date_text="Tuesday 5 May '26, 19:00", title="TT one"),
        make_event("e2", [("James C", 1520.0), ("Ann", 1560.0), ("Bea", 1610.0),
                          ("Cid", 1660.0), ("Dee", 1710.0)],
                   date_text="Tuesday 12 May '26, 19:00", title="TT two"),
    ]
    monkeypatch.setattr(server.store, "load_all", lambda: events)

    path = tmp_path / "identity.json"
    monkeypatch.setattr(idn, "IDENTITY_PATH", path)
    return events


def claim_james():
    """Confirm the "James Carron" group, leaving "James C" unclaimed."""
    return server.claim_athlete("name:james carron", "James Carron")


def athlete():
    return idn.Registry.load().find_by_display_name("James Carron")


# ---- searching for a stray result ----------------------------------------


def test_search_finds_every_spelling(world):
    rows = server.search_rows("james")
    assert {r["name"] for r in rows} == {"James Carron", "James C"}


def test_search_reports_who_owns_each_row(world):
    claim_james()
    rows = {r["name"]: r for r in server.search_rows("james")}
    assert rows["James Carron"]["owner"] == "James Carron"
    assert rows["James C"]["owner"] is None


def test_search_ignores_a_too_short_query(world):
    assert server.search_rows("j") == []
    assert server.search_rows("") == []


def test_search_returns_newest_first(world):
    dates = [r["date"] for r in server.search_rows("james")]
    assert dates == sorted(dates, reverse=True)


# ---- adding one result ---------------------------------------------------


def test_adopt_attaches_a_single_result(world):
    claim_james()
    before = len(athlete().claims)

    message = server.adopt_row(athlete().id, "e2", "2000")

    assert "James C" in message
    assert len(athlete().claims) == before + 1


def test_adopting_teaches_the_new_spelling(world):
    """The point of adopting: later races under that spelling resolve alone."""
    claim_james()
    server.adopt_row(athlete().id, "e2", "2000")
    assert athlete().name_variants == {"james carron", "james c"}


def test_adopt_refuses_an_unconfirmed_athlete(world):
    """A provisional group has no claims to add to."""
    with pytest.raises(LookupError, match="Confirm who this athlete is first"):
        server.adopt_row("name:james c", "e2", "2000")


def test_adopt_rejects_an_unknown_event_or_row(world):
    claim_james()
    with pytest.raises(LookupError, match="event is not in the local store"):
        server.adopt_row(athlete().id, "nope", "2000")
    with pytest.raises(LookupError, match="result is not in the event"):
        server.adopt_row(athlete().id, "e2", "999999")


# ---- releasing one result ------------------------------------------------


def test_disown_returns_the_row_to_its_original_name(world):
    """The whole contract: releasing restores what the entry list said."""
    claim_james()
    server.adopt_row(athlete().id, "e2", "2000")

    message = server.disown_row(athlete().id, "e2", "2000")

    assert "James C" in message
    assert athlete().name_variants == {"james carron"}

    resolutions = idn.resolve(world, idn.Registry.load())
    released = resolutions[("e2", "2000")]
    assert released.display_name == "James C"
    assert released.source == idn.PROVISIONAL


def test_disown_leaves_the_other_results_alone(world):
    claim_james()
    server.adopt_row(athlete().id, "e2", "2000")
    server.disown_row(athlete().id, "e2", "2000")
    assert len(athlete().claims) == 1


def test_disowning_the_last_result_removes_the_identity(world):
    """An athlete with no results left would sit empty in the standings."""
    claim_james()
    athlete_id = athlete().id

    message = server.disown_row(athlete_id, "e1", "2000")

    assert "no other results" in message
    assert idn.Registry.load().athletes == {}


def test_disown_rejects_a_row_it_does_not_own(world):
    claim_james()
    with pytest.raises(LookupError, match="not claimed by this athlete"):
        server.disown_row(athlete().id, "e2", "2000")


def test_disown_rejects_an_unknown_athlete(world):
    with pytest.raises(LookupError, match="No such athlete"):
        server.disown_row("ath_nope", "e1", "2000")


def test_adopt_then_disown_is_a_round_trip(world):
    """Correcting a mistake must leave no trace."""
    claim_james()
    before = sorted((c.event, c.race_id, c.name) for c in athlete().claims)

    server.adopt_row(athlete().id, "e2", "2000")
    server.disown_row(athlete().id, "e2", "2000")

    assert sorted((c.event, c.race_id, c.name) for c in athlete().claims) == before


def test_adopting_a_row_moves_it_from_another_athlete(world):
    """Two people cannot both own one result."""
    claim_james()
    server.claim_athlete("name:ann", "Ann Other")
    ann = idn.Registry.load().find_by_display_name("Ann Other")

    server.adopt_row(athlete().id, "e1", "2001")  # Ann's row in e1

    reloaded = idn.Registry.load()
    moved = reloaded.claim_owner("e1", "2001")
    assert moved == athlete().id
    assert all(c.key != ("e1", "2001") for c in reloaded.athletes[ann.id].claims)
