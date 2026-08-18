"""Tests pinned to a real aquathon page (https://raceclocker.com/dd7293a5).

The aquathon is the structural counterpart to the time trial and exercises the
parts of the parser the TT cannot: two timed legs, a mass start, and split slot
labels that do not match how the slots are actually used.
"""

from pathlib import Path

import pytest

from ctc_bot import classify as cls
from ctc_bot import raceclocker as rc

FIXTURE = Path(__file__).parent / "fixtures_dd7293a5.html"


@pytest.fixture(scope="module")
def event():
    return rc.parse(FIXTURE.read_text(encoding="utf-8"), "dd7293a5")


def test_event_metadata(event):
    assert event.title == "Aquathon 18th June"
    assert event.date_text == "Thursday 18 Jun '26, 19:00"
    assert event.distance == 8.0
    assert len(event.results) == 9


def test_two_timed_legs(event):
    assert event.timing_points == 3
    assert event.segments == 2


def test_classified_as_aquathon_on_all_three_signals(event):
    """Inferred alone, with no admin metadata: weekday, legs and title agree."""
    result = cls.classify(event)
    assert result.race_type == cls.AQUATHON
    assert result.confident
    inferred = {k: v for k, v in result.signals.items() if k != "listing"}
    assert set(inferred.values()) == {cls.AQUATHON}


def test_split_names_are_a_template_not_ground_truth(event):
    """The finish is recorded in a slot labelled "Run start"; slot 6 is empty.

    Guards the decision to derive legs from populated slots rather than labels.
    """
    assert event.split_names[5] == "Finish"
    assert all(row["TmSplit6"] == rc.EMPTY_SPLIT for row in event.results)
    assert rc.populated_slots(event.results[0]) == [1, 2, 5]


def test_mass_start(event):
    """Every athlete shares one start time, unlike the TT's individual starts."""
    assert len({row["TmSplit1"] for row in event.results}) == 1


@pytest.mark.parametrize("code", ["7eecd645", "dd7293a5"])
def test_computed_elapsed_matches_official_result(code):
    """Leg arithmetic must reproduce RaceClocker's own total, deci-seconds included."""
    html = (Path(__file__).parent / f"fixtures_{code}.html").read_text(encoding="utf-8")
    event = rc.parse(html, code)
    for row in event.results:
        assert rc.elapsed_seconds(row) == pytest.approx(float(row["TmResultSec"]), abs=0.05)


def test_recomputed_positions_are_in_time_order(event):
    standings = rc.ranked(event.results)
    times = [float(row["TmResultSec"]) for row in standings]
    assert times == sorted(times)
    assert standings[0]["Name"] == "Anthony"
    assert [row["Position"] for row in standings] == list(range(1, len(standings) + 1))


def test_legs_sum_to_total(event):
    for row in event.results:
        legs = rc.leg_seconds(row)
        assert len(legs) == 2
        assert sum(legs) == pytest.approx(float(row["TmResultSec"]), abs=0.05)
