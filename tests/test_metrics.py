"""Tests for the metrics engine.

The rules that matter are the ones that stop a plausible-looking number being
wrong: which distance a speed is computed from, when a field statistic is
withheld, and when a trend line is refused.
"""

from datetime import date, timedelta

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


# ---- plausibility --------------------------------------------------------


def test_impossible_results_are_dropped():
    """Hand timing misfires both ways, and both wreck a trend.

    Real examples from the club's history: a start and stop pressed together
    gave a 7-second time trial (7,097 km/h), and a timer left running gave a
    24-hour aquathon.
    """
    rows = [("Ann", 1500.0), ("Bea", 1600.0), ("Cid", 1700.0),
            ("Dee", 1800.0), ("Eve", 1900.0), ("Ghost", 7.0), ("Slow", 20000.0)]
    athletes = metrics.build([make_event("plaus", rows)], idn.Registry())

    assert "name:ghost" not in athletes
    assert "name:slow" not in athletes
    assert "name:ann" in athletes


def test_impossible_results_are_kept_out_of_the_field_mean():
    """One 7,097 km/h entry would wreck every z-score in that race."""
    clean = [("Ann", 1500.0), ("Bea", 1600.0), ("Cid", 1700.0),
             ("Dee", 1800.0), ("Eve", 1900.0)]
    with_error = clean + [("Ghost", 7.0)]

    a = metrics.build([make_event("c1", clean)], idn.Registry())["name:ann"]
    b = metrics.build([make_event("c2", with_error)], idn.Registry())["name:ann"]

    assert a.performances[0].field_size == b.performances[0].field_size == 5
    assert a.performances[0].z_score == pytest.approx(b.performances[0].z_score)


def test_positions_are_recomputed_after_filtering():
    """A dropped row must not leave a gap in the finishing order."""
    rows = [("Ghost", 7.0), ("Ann", 1500.0), ("Bea", 1600.0),
            ("Cid", 1700.0), ("Dee", 1800.0), ("Eve", 1900.0)]
    athletes = metrics.build([make_event("gap", rows)], idn.Registry())
    positions = sorted(p.position for a in athletes.values() for p in a.performances)
    assert positions == [1, 2, 3, 4, 5]


def test_an_event_of_mostly_errors_is_excluded_whole():
    """Two thirds implying 52+ km/h means the event is wrong, not the riders."""
    rows = [("A", 890.0), ("B", 880.0), ("C", 870.0), ("D", 1500.0), ("E", 1600.0)]
    event = make_event("broken", rows)
    verdict = curation.assess_event(event)
    assert verdict.excluded
    assert "implausible" in verdict.reason


def test_a_few_bad_rows_do_not_condemn_a_good_event():
    rows = [(f"R{i}", 1500.0 + i * 20) for i in range(10)] + [("Ghost", 7.0)]
    event = make_event("mostly_ok", rows)
    assert curation.assess_event(event).include


# ---- how hard was tonight? -----------------------------------------------


SERIES = f"{cls.TIME_TRIAL}|Short (13 km)"


def _weekly(counts, *, start_day=1, month=9):
    """Race dates a week apart, in the format stored events carry."""
    days = []
    for index in range(counts):
        when = date(2026, month, start_day) + timedelta(days=7 * index)
        days.append(f"{when:%A} {when.day} {when:%b} '{when:%y}, 19:00")
    return days


def _season(regulars, *, tonight_factor=1.0, weeks=5):
    """A run of weekly races where everyone holds a steady time, then one night
    that is ``tonight_factor`` times slower for the whole field."""
    dates = _weekly(weeks)
    events = []
    for index, date_text in enumerate(dates):
        last = index == len(dates) - 1
        factor = tonight_factor if last else 1.0
        rows = [(name, base * factor) for name, base in regulars]
        events.append(make_event(f"e{index}", rows, date_text=date_text))
    return events, f"e{len(dates) - 1}"


def _conditions(regulars, **kwargs):
    events, code = _season(regulars, **kwargs)
    athletes = metrics.build(events, idn.Registry())
    return metrics.race_conditions(athletes, SERIES, code)


SIX = [(f"R{i}", 1500.0 + i * 30) for i in range(6)]


def test_a_night_everyone_was_slower_reads_as_hard():
    """The point of the measure: separate the evening from the athlete."""
    found = _conditions(SIX, tonight_factor=1.04)
    assert found is not None
    assert found.regulars == 6
    assert found.percent < -3
    assert found.verdict == "hard"


def test_a_night_everyone_was_faster_reads_as_fast():
    found = _conditions(SIX, tonight_factor=0.96)
    assert found.percent > 3
    assert found.verdict == "fast"


def test_an_ordinary_night_reads_as_typical():
    found = _conditions(SIX)
    assert found.percent == pytest.approx(0.0, abs=0.2)
    assert found.verdict == "typical"


def test_one_persons_shocker_does_not_colour_the_evening():
    """A median across athletes, so a single bad ride cannot move the verdict."""
    events, code = _season(SIX)
    # Give the last event's first finisher a disastrous ride.
    for row in events[-1].event.results:
        if row["Name"] == "R0":
            row["TmResultSec"] = str(1500.0 * 1.5)
    athletes = metrics.build(events, idn.Registry())
    found = metrics.race_conditions(athletes, SERIES, code)
    assert found.verdict == "typical"


def test_too_few_regulars_says_nothing_at_all():
    """With four people the figure is one person's bad day in a lab coat."""
    four = [(f"R{i}", 1500.0 + i * 30) for i in range(4)]
    assert _conditions(four, tonight_factor=1.04) is None


def test_an_athlete_without_a_nearby_baseline_does_not_count():
    """Someone racing for the first time has nothing to be compared against."""
    events, code = _season(SIX)
    newcomer = {**events[-1].event.results[0], "RaceID": "9999", "Name": "Newcomer"}
    events[-1].event.results.append(newcomer)
    athletes = metrics.build(events, idn.Registry())
    assert metrics.race_conditions(athletes, SERIES, code).regulars == 6


def test_a_baseline_is_taken_from_nearby_races_not_the_whole_history():
    """Otherwise a club that gets fitter across a season would read as a season
    of steadily improving weather."""
    assert metrics.CONDITIONS_WINDOW_DAYS <= 90

    old = _weekly(3, month=1)          # January
    recent = _weekly(4, month=9)       # September, well outside the window
    events = []
    for index, date_text in enumerate(old):
        events.append(make_event(f"o{index}", [(n, s * 1.10) for n, s in SIX],
                                 date_text=date_text))
    for index, date_text in enumerate(recent):
        events.append(make_event(f"r{index}", SIX, date_text=date_text))

    athletes = metrics.build(events, idn.Registry())
    found = metrics.race_conditions(athletes, SERIES, "r3")
    # Judged against September, where everyone was going the same speed - not
    # against a January the whole club has since left behind.
    assert found.percent == pytest.approx(0.0, abs=0.2)
