"""Tests for local corrections layered over the archived RaceClocker data.

The contract that matters: a correction never touches the source, is applied
before anything is computed so the whole race stays consistent, and can always
be undone back to what the timing system published.
"""

import pytest

from ctc_bot import identity as idn
from ctc_bot import metrics
from ctc_bot import overrides as ovr
from tests.test_metrics import make_event


@pytest.fixture
def events():
    return [
        make_event("e1", [("Ann", 1800.0), ("Bea", 1850.0), ("Cid", 1900.0),
                          ("Dee", 1950.0), ("Eve", 2000.0)],
                   date_text="Tuesday 5 May '26, 19:00", title="TT one"),
    ]


@pytest.fixture
def corrections(tmp_path, monkeypatch):
    monkeypatch.setattr(ovr, "OVERRIDES_PATH", tmp_path / "overrides.json")
    return ovr.Overrides()


# ---- corrected times -----------------------------------------------------


def test_correction_changes_the_time(events, corrections):
    corrections.edit_time("e1", "2000", 1700.0, 1800.0)
    ann = metrics.build(events, idn.Registry(), corrections)["name:ann"].performances[0]
    assert ann.seconds == 1700.0
    assert ann.edited
    assert ann.original_seconds == 1800.0


def test_correction_never_touches_the_stored_event(events, corrections):
    """The archive must stay exactly as RaceClocker served it."""
    corrections.edit_time("e1", "2000", 1700.0, 1800.0)
    metrics.build(events, idn.Registry(), corrections)
    assert events[0].event.results[0]["TmResultSec"] == "1800.0"
    assert "_edited" not in events[0].event.results[0]


def test_correction_flows_into_the_whole_race(events, corrections):
    """Position, speed and the field mean must all agree with the new time.

    A corrected time that left everyone else's z-score measured against the old
    mean would be worse than no correction at all.
    """
    before = metrics.build(events, idn.Registry(), ovr.Overrides())
    corrections.edit_time("e1", "2004", 1000.0, 2000.0)  # Eve, last, now fastest
    after = metrics.build(events, idn.Registry(), corrections)

    assert before["name:eve"].performances[0].position == 5
    assert after["name:eve"].performances[0].position == 1
    # everyone else moves down one, and the field mean shifts for all of them
    assert after["name:ann"].performances[0].position == 2
    assert after["name:ann"].performances[0].z_score != before["name:ann"].performances[0].z_score


def test_reset_restores_the_published_time(events, corrections):
    corrections.edit_time("e1", "2000", 1700.0, 1800.0)
    corrections.reset_time("e1", "2000")
    ann = metrics.build(events, idn.Registry(), corrections)["name:ann"].performances[0]
    assert ann.seconds == 1800.0
    assert not ann.edited


def test_correcting_twice_still_resets_to_the_source(corrections):
    """The original is the published time, never the previous correction."""
    corrections.edit_time("e1", "2000", 1700.0, 1800.0)
    corrections.edit_time("e1", "2000", 1650.0, 1700.0)
    assert corrections.edit_for("e1", "2000").original_seconds == 1800.0


def test_a_correction_must_be_positive(corrections):
    with pytest.raises(ValueError):
        corrections.edit_time("e1", "2000", 0.0, 1800.0)


# ---- hand-added races ----------------------------------------------------


def test_added_race_appears_in_the_athletes_history(events, corrections):
    athletes = metrics.build(events, idn.Registry(), ovr.Overrides())
    ann_id = athletes["name:ann"].athlete_id
    corrections.add_result(ann_id, "time_trial", "2026-06-02", 1750.0, title="Timer missed me")

    ann = metrics.build(events, idn.Registry(), corrections)["name:ann"]
    added = next(p for p in ann.performances if p.manual)
    assert added.seconds == 1750.0
    assert added.event_title == "Timer missed me"
    assert added.speed_kmh is not None


def test_added_race_carries_no_field_statistics(events, corrections):
    """There is no field it was measured against, so none are invented."""
    corrections.add_result("name:ann", "time_trial", "2026-06-02", 1750.0)
    ann = metrics.build(events, idn.Registry(), corrections)["name:ann"]
    added = next(p for p in ann.performances if p.manual)
    assert added.field_size == 0
    assert added.z_score is None
    assert added.percentile is None


def test_added_race_counts_towards_the_trend(events, corrections):
    corrections.add_result("name:ann", "time_trial", "2026-06-02", 1750.0)
    corrections.add_result("name:ann", "time_trial", "2026-06-09", 1700.0)
    ann = metrics.build(events, idn.Registry(), corrections)["name:ann"]
    series = ann.series_keys[0]
    assert ann.can_trend(series)


def test_removing_an_added_race_leaves_nothing_behind(events, corrections):
    addition = corrections.add_result("name:ann", "time_trial", "2026-06-02", 1750.0)
    corrections.remove_result(addition.id)
    ann = metrics.build(events, idn.Registry(), corrections)["name:ann"]
    assert not any(p.manual for p in ann.performances)


def test_added_race_for_an_unknown_athlete_is_ignored(events, corrections):
    """Releasing an athlete must not crash the build."""
    corrections.add_result("ath_gone", "time_trial", "2026-06-02", 1750.0)
    athletes = metrics.build(events, idn.Registry(), corrections)
    assert not any(p.manual for a in athletes.values() for p in a.performances)


@pytest.mark.parametrize("when", ["not-a-date", "2026-13-01", ""])
def test_added_race_rejects_a_bad_date(corrections, when):
    with pytest.raises(ValueError):
        corrections.add_result("name:ann", "time_trial", when, 1750.0)


# ---- persistence ---------------------------------------------------------


def test_round_trips_through_disk(tmp_path):
    path = tmp_path / "overrides.json"
    original = ovr.Overrides()
    original.edit_time("e1", "2000", 1700.0, 1800.0)
    original.add_result("name:ann", "aquathon", "2026-06-02", 1500.0)
    original.save(path)

    reloaded = ovr.Overrides.load(path)
    assert reloaded.edit_for("e1", "2000").seconds == 1700.0
    assert len(reloaded.additions) == 1
    assert reloaded.additions[0].race_type == "aquathon"


def test_missing_file_means_no_corrections(tmp_path):
    assert ovr.Overrides.load(tmp_path / "nothing.json").edits == {}
