"""Tests for the dashboard payload, the page, and the claim API.

The page is generated from real stored events, so these tests focus on the
contract the page relies on and on the things that would quietly break it:
a non-self-contained page, unescaped names, or a claim that writes nothing.
"""

import json
import re

import pytest

from ctc_bot import classify as cls
from ctc_bot import dashboard
from ctc_bot import identity as idn
from ctc_bot import raceclocker as rc
from ctc_bot import store
from tests.test_metrics import make_event


@pytest.fixture
def events():
    return [
        make_event("e1", [("Ann", 1800.0), ("Bea", 1850.0), ("Cid", 1900.0),
                          ("Dee", 1950.0), ("Eve", 2000.0)],
                   date_text="Tuesday 3 Sep '24, 19:00", title="TT one"),
        make_event("e2", [("Ann", 1750.0), ("Bea", 1860.0), ("Cid", 1890.0),
                          ("Dee", 1940.0), ("Eve", 1990.0)],
                   date_text="Tuesday 2 Sep '25, 19:00", title="TT two"),
        make_event("e3", [("Ann", 1700.0), ("Bea", 1870.0), ("Cid", 1880.0),
                          ("Dee", 1930.0), ("Eve", 1980.0)],
                   date_text="Tuesday 1 Sep '26, 19:00", title="TT three"),
    ]


@pytest.fixture
def payload(events):
    return dashboard.build_payload(events, idn.Registry())


# ---- payload -------------------------------------------------------------


def test_summary_counts(payload):
    assert payload["summary"]["races"] == 3
    assert payload["summary"]["athletes"] == 5
    assert payload["summary"]["results"] == 15


def test_athletes_are_ordered_by_race_count(payload):
    counts = [a["races"] for a in payload["athletes"]]
    assert counts == sorted(counts, reverse=True)


def test_trend_present_only_above_the_threshold(payload):
    ann = next(a for a in payload["athletes"] if a["name"] == "Ann")
    assert ann["races"] == 3 >= payload["summary"]["minRacesForTrend"]
    assert ann["trends"], "three races should yield a fitted trend"
    slope = next(iter(ann["trends"].values()))["slope"]
    assert slope > 0  # Ann got faster every year


def only_series(payload):
    assert len(payload["series"]) == 1
    return payload["series"][0]


def test_latest_race_is_the_most_recent(payload):
    latest = only_series(payload)["latest"]
    assert latest["title"] == "TT three"
    assert latest["rows"][0]["position"] == 1


def test_latest_race_reports_change_against_the_athletes_own_average(payload):
    ann = next(r for r in only_series(payload)["latest"]["rows"] if r["name"] == "Ann")
    # 1700 against a prior mean of (1800+1750)/2 = 1775 -> 75s faster
    assert ann["vsAverage"] == pytest.approx(-75.0)


def test_first_race_has_no_comparison(events):
    payload = dashboard.build_payload(events[:1], idn.Registry())
    assert all(row["vsAverage"] is None for row in only_series(payload)["latest"]["rows"])


# ---- series tabs ---------------------------------------------------------


def test_each_series_gets_its_own_tab(events):
    """One tab per enabled series, each with its own counts and latest race."""
    events.append(
        make_event("aq1", [("Ann", 1560.0), ("Bea", 1600.0), ("Cid", 1700.0),
                           ("Dee", 1800.0), ("Eve", 1900.0)],
                   race_type=cls.AQUATHON, listed_km=8.0,
                   date_text="Thursday 4 Sep '26, 19:00", title="Aquathon one")
    )
    payload = dashboard.build_payload(events, idn.Registry())
    keys = [s["key"] for s in payload["series"]]

    assert "aquathon" in keys
    assert any(k.startswith("time_trial|") for k in keys)
    for series in payload["series"]:
        assert series["races"] >= 1
        assert series["latest"]["rows"]


def test_aquathon_tab_comes_first(events):
    events.append(
        make_event("aq2", [("Ann", 1560.0)], race_type=cls.AQUATHON, listed_km=8.0,
                   date_text="Thursday 4 Sep '26, 19:00", title="Aquathon one")
    )
    payload = dashboard.build_payload(events, idn.Registry())
    assert payload["series"][0]["key"] == "aquathon"


def test_a_disabled_route_is_absent_from_the_page(events, monkeypatch):
    """The retired 13.8 km route is hidden, not deleted."""
    from ctc_bot import config

    events.append(
        make_event("long1", [("Ann", 1900.0)], listed_km=13.8,
                   date_text="Tuesday 15 Sep '26, 19:00", title="Long route TT")
    )
    payload = dashboard.build_payload(events, idn.Registry())

    keys = [s["key"] for s in payload["series"]]
    assert "time_trial|Long (13.8 km)" not in keys
    assert any(h["distance"] == 13.8 for h in payload["hidden"])

    # and no athlete carries a run from the hidden series
    for athlete in payload["athletes"]:
        assert all(run["series"] in keys for run in athlete["runs"])


def test_enabling_a_route_brings_its_history_back(events, tmp_path):
    """Hiding is reversible with no refetch - the events were never dropped."""
    from ctc_bot import config

    events.append(
        make_event("long2", [("Zoe", 1900.0)], listed_km=13.8,
                   date_text="Tuesday 15 Sep '26, 19:00", title="Long route TT")
    )
    assert not any(a["name"] == "Zoe" for a in dashboard.build_payload(events, idn.Registry())["athletes"])

    path = tmp_path / "courses.json"
    courses = config.load_courses()
    for route in courses[cls.TIME_TRIAL].routes:
        route.enabled = True
    config.save_courses(courses, path)

    original = config.CONFIG_PATH
    config.CONFIG_PATH = path
    try:
        payload = dashboard.build_payload(events, idn.Registry())
    finally:
        config.CONFIG_PATH = original

    assert any(a["name"] == "Zoe" for a in payload["athletes"])
    assert "time_trial|Long (13.8 km)" in [s["key"] for s in payload["series"]]


def test_standings_are_ranked_by_speed(payload):
    series = next(iter(payload["standings"]))
    names = [row["name"] for row in payload["standings"][series]]
    assert names[0] == "Ann"


def test_unverified_athletes_are_flagged(payload):
    assert all(a["verified"] is False for a in payload["athletes"])


def test_excluded_events_are_listed_with_reasons(events):
    events.append(make_event("copy", [("Ann", 1.0)], title="(Copy of) TT one"))
    payload = dashboard.build_payload(events, idn.Registry())
    assert any(e["reason"] == "copy" for e in payload["excluded"])


def test_payload_is_json_serialisable(payload):
    json.dumps(payload)  # must not raise


# ---- page ----------------------------------------------------------------


@pytest.fixture
def page(payload):
    return dashboard.render(payload)


def test_page_embeds_the_data(page):
    match = re.search(r"const DATA = (\{.*?\});\n", page, re.S)
    assert match
    assert json.loads(match.group(1))["summary"]["races"] == 3


def test_page_makes_no_external_requests(page):
    """It must open from a file with no network at all."""
    for marker in ("http://", "https://", "src=\"//", "cdn."):
        assert marker not in page


def test_page_declares_both_themes(page):
    assert "prefers-color-scheme: dark" in page
    assert '[data-theme="dark"]' in page


def test_page_has_a_table_view_for_every_chart(page):
    """Three palette slots sit under 3:1 contrast, so charts need label relief."""
    assert "<table" in page
    assert "aria-label" in page


def test_names_are_escaped(events):
    events.append(
        make_event("xss", [("<script>alert(1)</script>", 1800.0)],
                   date_text="Tuesday 8 Sep '26, 19:00")
    )
    page = dashboard.render(dashboard.build_payload(events, idn.Registry()))
    assert "<script>alert(1)</script>" not in page.replace(
        '"<script>alert(1)<\\/script>"', ""
    )


def test_build_writes_a_file(tmp_path, monkeypatch, events):
    monkeypatch.setattr(store, "load_all", lambda: events)
    monkeypatch.setattr(idn.Registry, "load", classmethod(lambda cls_, path=None: idn.Registry()))
    target = tmp_path / "out" / "dashboard.html"
    written = dashboard.build(out_path=target)
    assert written.exists() and written.stat().st_size > 10_000


# ---- series labels -------------------------------------------------------


@pytest.mark.parametrize(
    "key, expected",
    [
        ("aquathon", "Aquathon"),
        ("time_trial|Short (13 km)", "Time trial — Short (13 km)"),
        ("time_trial", "Time trial"),
    ],
)
def test_series_labels(key, expected):
    assert dashboard.series_label(key) == expected
