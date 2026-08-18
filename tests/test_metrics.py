"""Tests for the metrics engine.

The rules that matter are the ones that stop a plausible-looking number being
wrong: which distance a speed is computed from, when a field statistic is
withheld, and when a trend line is refused.
"""

from datetime import date

import pytest

from ctc_bot import classify as cls
from ctc_bot import curation
from ctc_bot import identity as idn
from ctc_bot import metrics
from ctc_bot import raceclocker as rc
from ctc_bot import store


def make_event(code, rows, *, race_type=cls.TIME_TRIAL, date_text="Tuesday 1 Sep '26, 19:00",
               listed_km=13.0, title="Synthetic TT"):
    """Build a StoredEvent with real finish times."""
    results = []
    for index, (name, seconds) in enumerate(rows):
        finish_h = 19 + int((60 + seconds) // 3600)
        results.append(
            {
                "RaceID": str(2000 + index),
                "Name": name,
                "Bib": str(index + 1),
                "Rank": str(index + 1),
                "Result": "00:00:00.0",
                "TmResultSec": str(seconds),
                "TmSplit1": "19:00:00",
                "TmSplit1dc": "0",
                "TmSplit5": f"{finish_h:02d}:{int(seconds // 60) % 60:02d}:{int(seconds % 60):02d}",
                "TmSplit5dc": "0",
            }
        )
    event = rc.Event(
        code=code, title=title, date_text=date_text,
        distance=listed_km, distance_unit="km", results=results,
    )
    return store.StoredEvent(event, race_type, True, {"listed_distance_km": listed_km})


# ---- dates ---------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Thursday 16 Jul '26, 19:00", date(2026, 7, 16)),
        ("Tuesday 1 Sep '20, 19:00", date(2020, 9, 1)),
        ("Saturday 4 Jul '19, 08:00", date(2019, 7, 4)),
        ("nonsense", None),
        (None, None),
    ],
)
def test_parse_date(text, expected):
    assert metrics.parse_date(text) == expected


# ---- speed uses the route, not the page ----------------------------------


def test_speed_uses_the_route_distance():
    """A 26-minute ride scores differently on the two courses, correctly so."""
    short = make_event("s1", [("Ann", 1560.0)], listed_km=13.0)
    long = make_event("l1", [("Ann", 1560.0)], listed_km=13.8)

    short_speed = metrics.build([short], idn.Registry())["name:ann"].performances[0].speed_kmh
    long_speed = metrics.build([long], idn.Registry())["name:ann"].performances[0].speed_kmh

    assert short_speed == pytest.approx(30.0, abs=0.01)
    assert long_speed == pytest.approx(31.85, abs=0.01)


def test_page_wobble_within_a_route_does_not_change_speed():
    """13.0 and 13.1 are the same road."""
    a = make_event("a", [("Ann", 1560.0)], listed_km=13.0)
    b = make_event("b", [("Ann", 1560.0)], listed_km=13.1)
    speeds = {
        metrics.build([e], idn.Registry())["name:ann"].performances[0].speed_kmh
        for e in (a, b)
    }
    assert len(speeds) == 1


def test_routes_form_separate_series():
    """Times on different courses must never share a trend line."""
    short = make_event("s2", [("Ann", 1560.0)], listed_km=13.0)
    long = make_event("l2", [("Ann", 1500.0)], listed_km=13.8)
    ann = metrics.build([short, long], idn.Registry())["name:ann"]
    assert len(ann.series_keys) == 2


# ---- field statistics ----------------------------------------------------


def test_field_stats_withheld_for_a_tiny_field():
    """A z-score from a two-person race is noise presented as signal."""
    tiny = make_event("tiny", [("Ann", 1500.0), ("Bea", 1600.0)])
    performance = metrics.build([tiny], idn.Registry())["name:ann"].performances[0]
    assert performance.field_size == 2
    assert performance.z_score is None
    assert performance.percentile is None
    # the raw numbers are still there
    assert performance.seconds == 1500.0
    assert performance.speed_kmh is not None


def test_field_stats_present_for_a_real_field():
    rows = [(f"R{i}", 1500.0 + i * 30) for i in range(curation.MIN_FIELD_FOR_STATS)]
    event = make_event("big", rows)
    athletes = metrics.build([event], idn.Registry())
    fastest = athletes["name:r0"].performances[0]
    slowest = athletes[f"name:r{curation.MIN_FIELD_FOR_STATS - 1}"].performances[0]

    assert fastest.z_score < 0 < slowest.z_score  # negative is faster than the field
    assert fastest.percentile == 100.0
    assert slowest.percentile == 0.0


def test_placeholders_count_towards_the_field_but_are_not_athletes():
    """They really did race, so a z-score should reflect them."""
    rows = [("Ann", 1500.0), ("Unknown", 1550.0), ("Bea", 1600.0),
            ("Name", 1650.0), ("Cid", 1700.0)]
    event = make_event("mixed", rows)
    athletes = metrics.build([event], idn.Registry())

    assert set(athletes) == {"name:ann", "name:bea", "name:cid"}
    assert athletes["name:ann"].performances[0].field_size == 5


# ---- trends --------------------------------------------------------------


def _three_race_athlete():
    events = [
        make_event("t1", [("Ann", 1800.0)] + [(f"P{i}", 1700.0 + i) for i in range(5)],
                   date_text="Tuesday 1 Sep '24, 19:00"),
        make_event("t2", [("Ann", 1700.0)] + [(f"P{i}", 1700.0 + i) for i in range(5)],
                   date_text="Tuesday 2 Sep '25, 19:00"),
        make_event("t3", [("Ann", 1600.0)] + [(f"P{i}", 1700.0 + i) for i in range(5)],
                   date_text="Tuesday 1 Sep '26, 19:00"),
    ]
    return metrics.build(events, idn.Registry())["name:ann"]


def test_trend_refused_below_the_minimum():
    events = [
        make_event("u1", [("Ann", 1800.0)], date_text="Tuesday 1 Sep '25, 19:00"),
        make_event("u2", [("Ann", 1700.0)], date_text="Tuesday 1 Sep '26, 19:00"),
    ]
    ann = metrics.build(events, idn.Registry())["name:ann"]
    series = ann.series_keys[0]
    assert ann.race_count == 2 < idn.MIN_RACES_FOR_TREND
    assert not ann.can_trend(series)
    assert ann.trend(series) is None


def test_improvement_reads_as_a_positive_slope():
    """Fitted on speed, so getting faster always slopes upward."""
    ann = _three_race_athlete()
    series = ann.series_keys[0]
    assert ann.can_trend(series)
    slope, r2 = ann.trend(series)
    assert slope > 0
    assert 0 <= r2 <= 1


def test_personal_best_is_per_series():
    """A best on the short course is not a best on the long one."""
    short = make_event("ps", [("Ann", 1560.0)], listed_km=13.0)
    long = make_event("pl", [("Ann", 1900.0)], listed_km=13.8,
                      date_text="Tuesday 8 Sep '26, 19:00")
    ann = metrics.build([short, long], idn.Registry())["name:ann"]
    assert all(p.is_personal_best for p in ann.performances)


def test_personal_best_picks_the_fastest_in_a_series():
    ann = _three_race_athlete()
    best = [p for p in ann.performances if p.is_personal_best]
    assert len(best) == 1
    assert best[0].seconds == 1600.0


# ---- aggregate views -----------------------------------------------------


def test_standings_are_ordered_by_best_speed():
    event = make_event("st", [("Ann", 1500.0), ("Bea", 1600.0), ("Cid", 1700.0),
                              ("Dee", 1800.0), ("Eve", 1900.0)])
    athletes = metrics.build([event], idn.Registry())
    series = next(iter(athletes.values())).series_keys[0]
    table = metrics.standings(athletes, series)
    assert [a.display_name for a, _, _ in table] == ["Ann", "Bea", "Cid", "Dee", "Eve"]


def test_latest_event_is_the_most_recent():
    old = make_event("old", [("Ann", 1500.0)], date_text="Tuesday 1 Sep '24, 19:00")
    new = make_event("new", [("Ann", 1500.0)], date_text="Tuesday 1 Sep '26, 19:00")
    when, stored = metrics.latest_event([old, new])
    assert stored.code == "new"
    assert when == date(2026, 9, 1)


def test_excluded_events_never_reach_the_metrics():
    real = make_event("real", [("Ann", 1500.0)])
    copy = make_event("copy", [("Ann", 1400.0)], title="(Copy of) Synthetic TT")
    ann = metrics.build([real, copy], idn.Registry())["name:ann"]
    assert ann.race_count == 1
    assert ann.performances[0].seconds == 1500.0


def test_contested_athletes_are_shown_not_dropped():
    """A contested name must not vanish from the dashboard.

    Lorraine has 15 timed races and is contested; setting her athlete_id to
    None erased every one of them from the metrics, and she disappeared from
    the dashboard entirely.
    """
    rows = [("Lorraine", 1700.0), ("Lorraine", 1800.0), ("Ann", 1750.0),
            ("Bea", 1760.0), ("Cid", 1770.0)]
    event = make_event("cont", rows)
    athletes = metrics.build([event], idn.Registry())

    assert "contested:lorraine" in athletes
    lorraine = athletes["contested:lorraine"]
    assert lorraine.race_count == 2
    assert lorraine.contested is True
    assert lorraine.verified is False
