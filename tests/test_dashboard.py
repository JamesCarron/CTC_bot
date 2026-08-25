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


def test_time_trial_is_the_default_tab(events):
    """The time trial runs weekly and is what most people come to look at."""
    events.append(
        make_event("aq2", [("Ann", 1560.0)], race_type=cls.AQUATHON, listed_km=8.0,
                   date_text="Thursday 4 Sep '26, 19:00", title="Aquathon one")
    )
    payload = dashboard.build_payload(events, idn.Registry())
    assert payload["series"][0]["key"].startswith("time_trial")
    assert payload["series"][1]["key"] == "aquathon"


def test_the_route_is_not_named_when_only_one_is_shown(events):
    """With the 13.8 km route retired there is nothing to tell apart."""
    payload = dashboard.build_payload(events, idn.Registry())
    assert payload["series"][0]["label"] == "Time trial"


def test_the_route_is_named_when_two_are_shown(events, tmp_path):
    from ctc_bot import config

    events.append(
        make_event("long3", [("Ann", 1900.0)], listed_km=13.8,
                   date_text="Tuesday 15 Sep '26, 19:00", title="Long route TT")
    )
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

    labels = [s["label"] for s in payload["series"]]
    assert "Time trial — Short (13 km)" in labels
    assert "Time trial — Long (13.8 km)" in labels


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

    # and no athlete carries a run from the hidden series
    for athlete in payload["athletes"]:
        assert all(run["series"] in keys for run in athlete["runs"])

    # The events are still stored, which is what makes hiding reversible -
    # see test_enabling_a_route_brings_its_history_back.
    assert any(stored.code == "long1" for stored in events)


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


def test_standings_carry_only_what_the_page_shows(events):
    """A leaderboard is a top-of-the-table thing, and the tail is page weight
    for rows that never render."""
    many = events + [
        make_event(f"big{i}", [(f"Rider {n}", 1500.0 + n * 5) for n in range(30)],
                   date_text=f"Tuesday {i + 1} Sep '24, 19:00")
        for i in range(2)
    ]
    payload = dashboard.build_payload(many, idn.Registry())
    for series, rows in payload["standings"].items():
        assert len(rows) <= dashboard.STANDINGS_SHOWN, series
    assert any(len(rows) == dashboard.STANDINGS_SHOWN for rows in payload["standings"].values())
    # Everyone still has their own history and appears in the athlete list.
    assert len(payload["athletes"]) > dashboard.STANDINGS_SHOWN


def test_standings_are_ranked_by_speed(payload):
    series = next(iter(payload["standings"]))
    names = [row["name"] for row in payload["standings"][series]]
    assert names[0] == "Ann"


def test_unverified_athletes_are_flagged(payload):
    assert all(a["verified"] is False for a in payload["athletes"])


def test_excluded_events_are_counted_but_not_listed(events):
    """An excluded event is kept out of the figures and off the page.

    The full list with reasons was useful while curation was being tuned, but it
    only ever invited questions about races nobody was looking for. The count
    stays so the exclusion is still visible in the payload.
    """
    events.append(make_event("copy", [("Zelda Onlyhere", 1.0)], title="(Copy of) TT one"))
    payload = dashboard.build_payload(events, idn.Registry())
    assert "excluded" not in payload
    assert payload["summary"]["excluded"] == 1
    assert not any(a["name"] == "Zelda Onlyhere" for a in payload["athletes"])


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


def test_page_is_dark_only(page):
    """One look, tested once.

    A toggle meant every chart had to be checked against two surfaces, and the
    light palette had three slots below 3:1 contrast that needed label relief.
    """
    assert "color-scheme: dark" in page
    assert "prefers-color-scheme" not in page
    assert 'id="theme"' not in page


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


def test_route_suffix_is_dropped_on_request():
    assert dashboard.series_label(
        "time_trial|Short (13 km)", name_the_route=False
    ) == "Time trial"


# ---- the athlete panel ---------------------------------------------------


def test_page_offers_year_views_and_an_all_time_view(page):
    """The chart is split per season, defaulting to all time."""
    assert "year-tabs" in page
    assert "All time" in page
    assert 'state.year' in page


def test_athlete_table_drops_the_noisy_columns(page):
    """Race name, place and vs-field were removed from the per-athlete table."""
    assert "most recent first" in page
    assert "vs field" not in page


def test_claim_instructions_explain_the_name_problem(page):
    """A club member has to understand why their results are split up."""
    assert "entry sheet" in page
    assert "James Carrons" in page  # the real example that makes it concrete


# ---- portrait phones and the year default --------------------------------


def test_chart_is_sized_to_its_container(page):
    """A fixed 640-wide viewBox scaled to a 390px phone shrank the labels too.

    11px type rendered at about 6px, which is what made the chart unreadable in
    portrait. Matching the viewBox to the real width keeps text at its intended
    size on any screen.
    """
    assert "function chartWidth()" in page
    assert 'viewBox="0 0 ${W} ${H}"' in page
    assert "const narrow = W < 460" in page


def test_chart_redraws_when_the_screen_changes(page):
    """Sized at render time, so a rotation has to trigger a redraw."""
    assert 'addEventListener("resize"' in page
    # ...but not for the mobile URL bar sliding away, which fires resize
    # constantly while scrolling.
    assert "if (window.innerWidth === lastWidth) return;" in page


def test_there_is_a_portrait_breakpoint(page):
    assert "@media (max-width: 620px)" in page
    # the athlete list stops being a side column
    assert "grid-template-columns:1fr" in page


def test_year_view_defaults_to_all_time(page):
    """It opened on the latest season back when a multi-season chart was two
    thirds empty winter. Now that the axis breaks between seasons, the whole
    history is the more useful thing to land on, and a stale year falls back
    there too rather than silently changing the question."""
    assert 'year: null }' in page          # so an explicit choice is respected
    assert 'state.year = "all";' in page
    assert "state.year = years[0];" not in page


def test_a_single_season_chart_is_labelled_by_month(page):
    """Every point in one season shares a year, so a year axis says nothing."""
    assert "seasons.length === 1" in page
    assert '"Jan","Feb","Mar"' in page


def test_the_chart_axis_breaks_between_seasons(page):
    """Racing runs May to September and stops for the winter.

    On a plain timeline an all-time chart spends two thirds of its width
    drawing nothing, then joins August to the following May with a line
    implying a change in form over months when nobody raced. One column per
    season, one path per season, and a break mark in between.
    """
    # A path per season, never one path over every point.
    assert "const path = seasons.map(yr => {" in page
    assert 'const path = pts.map((p, i)' not in page
    # Gridlines and baseline drawn per column, so nothing bridges the break.
    assert "grid += seasons.map(yr =>" in page
    assert "const baseline = seasons.map(yr =>" in page
    assert "const breaks = seasons.slice(0, -1).map(yr =>" in page


def test_every_season_column_covers_the_same_calendar_span(page):
    """Otherwise June in one column would not line up with June in the next,
    and two seasons could not be read against each other."""
    assert "const dayOfYear = (iso) =>" in page
    assert "const d0 = Math.min(...days), d1 = Math.max(...days);" in page


def test_the_axis_unit_clears_the_top_tick(page):
    """At the old top margin the caption and the highest tick value were drawn
    at almost the same height and overprinted - "35" through "km/h"."""
    assert "y=\"${m.t - 9}\"" in page
    assert "{ t: 22, r: 10, b: 26, l: 34 }" in page   # narrow
    assert "{ t: 26, r: 18, b: 30, l: 46 }" in page   # wide


# ---- intro, honour note, and the aquathon warning ------------------------


def test_page_opens_with_no_athlete_selected(page):
    """Opening on whoever sorts first implies the page is about them.

    It also invites claiming the wrong person's races, which is the one
    mistake that is tedious to undo.
    """
    assert "Pick an athlete" in page
    assert "showAthlete(state.athlete ? people.find" in page


def test_intro_explains_why_results_are_split_up(page):
    """A visitor who does not know this reads the split histories as errors."""
    assert 'class="intro"' in page
    assert "entry sheet" in page


def test_honour_system_is_stated(page):
    """Anyone with the password can edit anything; say so rather than imply it."""
    assert "honour system" in page
    assert "only claim races you actually rode" in page


def test_aquathon_says_it_has_fewer_regulars(page):
    """Not a data-quality warning any more.

    Each leg is bounded separately now and the name variants have been worked
    through, so both of the original reasons are gone. What remains is who
    turns up: 197 people have raced one aquathon and never come back, which
    makes an evening there genuinely harder to compare.
    """
    assert "Fewer regulars." in page
    assert "race it once" in page
    assert 's.key === "aquathon"' in page   # and only there


def test_the_old_draft_warning_is_gone(page):
    """It claimed two things that are no longer true, and claiming them
    understated the data rather than being cautious about it."""
    assert "Rough draft." not in page
    assert "consolidate athletes" not in page
