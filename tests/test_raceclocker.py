"""Regression tests pinned to a real RaceClocker page.

The fixture is a verbatim snapshot of https://raceclocker.com/7eecd645. If
RaceClocker changes how it embeds results, these tests fail loudly rather than
the pipeline silently producing empty events.
"""

from pathlib import Path

import pytest

from ctc_bot import classify as cls
from ctc_bot import raceclocker as rc

FIXTURE = Path(__file__).parent / "fixtures_7eecd645.html"


@pytest.fixture(scope="module")
def event():
    return rc.parse(FIXTURE.read_text(encoding="utf-8"), "7eecd645")


def test_extracts_full_field(event):
    assert len(event.results) == 15
    assert event.results[0]["Name"] == "Gedis"
    assert event.results[0]["TmResultSec"] == "1274.0"


def test_extracts_event_metadata(event):
    assert event.title == "Timetrial 18th aug"
    assert event.date_text == "Tuesday 18 Aug '26, 19:00"
    assert event.distance == 13.0
    assert event.distance_unit == "km"


def test_split_structure_is_a_single_timed_leg(event):
    # Only the start (slot 1) and finish (slot 5) carry times.
    assert event.timing_points == 2
    assert event.segments == 1


def test_classified_as_time_trial(event):
    result = cls.classify(event)
    assert result.race_type == cls.TIME_TRIAL
    assert result.confident, result.notes


def test_source_rank_is_not_trustworthy(event):
    """RaceClocker's Rank field mirrors bib order here, not finish time.

    Documents why the pipeline recomputes rank from TmResultSec.
    """
    by_source_rank = [r["Name"] for r in sorted(event.results, key=lambda r: int(r["Rank"]))]
    by_time = [r["Name"] for r in sorted(event.results, key=lambda r: float(r["TmResultSec"]))]
    assert by_source_rank != by_time


@pytest.mark.parametrize(
    "text, expected",
    [
        ("https://raceclocker.com/7eecd645", ["7eecd645"]),
        ("RaceClocker.com/AB12CD34", ["ab12cd34"]),
        ("dup https://raceclocker.com/7eecd645 and /7eecd645", ["7eecd645"]),
        ("no links here", []),
    ],
)
def test_link_extraction(text, expected):
    assert rc.extract_event_codes(text) == expected


def test_classifier_flags_disagreement():
    """A Thursday event with a single timed leg must not be guessed at."""
    event = rc.Event(
        code="deadbeef",
        title=None,
        date_text="Thursday 20 Aug '26, 19:00",
        distance=5.0,
        distance_unit="km",
        results=[{"TmSplit1": "19:00:00", "TmSplit5": "19:20:00"}],
    )
    result = cls.classify(event)
    assert result.race_type == cls.UNKNOWN
    assert result.needs_review
