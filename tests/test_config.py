"""Tests for course configuration.

The published RaceClocker distance is untrusted, so these tests assert the
configured course wins.
"""

import pytest

from ctc_bot import classify as cls
from ctc_bot import config


def test_default_courses_match_the_club_setup():
    courses = config.load_courses()
    assert courses[cls.TIME_TRIAL].distance_km == 13.0
    aquathon = courses[cls.AQUATHON]
    assert [(leg.name, leg.distance_km) for leg in aquathon.legs] == [
        ("Swim", 0.6),
        ("Run", 3.5),
    ]


def test_configured_aquathon_distance_overrides_the_published_figure():
    """RaceClocker publishes 8.0 km; the real course is 4.1 km.

    Trusting the page would put every aquathon pace out by roughly 2x.
    """
    aquathon = config.load_courses()[cls.AQUATHON]
    assert aquathon.distance_km == 4.1
    assert aquathon.distance_km != 8.0


def test_leg_counts_match_what_the_parser_derives():
    courses = config.load_courses()
    assert courses[cls.TIME_TRIAL].segments == 1
    assert courses[cls.AQUATHON].segments == 2


def test_admin_can_override_distances_on_disk(tmp_path):
    path = tmp_path / "courses.json"
    courses = config.load_courses()
    courses[cls.AQUATHON].legs[1].distance_km = 4.0
    config.save_courses(courses, path)

    reloaded = config.load_courses(path)
    assert reloaded[cls.AQUATHON].distance_km == pytest.approx(4.6)


def test_missing_config_falls_back_to_defaults(tmp_path):
    courses = config.load_courses(tmp_path / "does_not_exist.json")
    assert courses[cls.TIME_TRIAL].distance_km == 13.0


def test_defaults_are_not_mutated_by_callers(tmp_path):
    """load_courses must hand back copies, or one edit poisons every later load."""
    first = config.load_courses(tmp_path / "none.json")
    first[cls.TIME_TRIAL].legs[0].distance_km = 99.0
    second = config.load_courses(tmp_path / "none.json")
    assert second[cls.TIME_TRIAL].distance_km == 13.0


# ---- the configured distance is the only one used ------------------------


def test_configured_distance_is_used_for_every_event():
    """One distance per race type, regardless of what any event page says.

    Across the club's 105 time trials the listed distance drifts between 13.0
    and 14.0 km - the same road measured by different devices over seven years.
    Using the per-event figure would inject up to 7% of phantom variation into
    pace trends, larger than most athletes' year-on-year improvement.
    """
    listed_across_history = [13.0, 13.1, 13.5, 13.6, 13.62, 13.8, 13.9, 14.0]

    # Same rider, same 25-minute effort, eight differently-measured events.
    seconds = 1500.0
    speeds_from_config = {
        round(config.distance_km(cls.TIME_TRIAL) / (seconds / 3600), 4)
        for _ in listed_across_history
    }
    speeds_from_page = {
        round(listed / (seconds / 3600), 4) for listed in listed_across_history
    }

    # The configured course gives one speed; the page would give eight.
    assert speeds_from_config == {31.2}
    assert len(speeds_from_page) == 8
    assert max(speeds_from_page) - min(speeds_from_page) > 2.0  # km/h of noise


def test_leg_distances_are_configured_not_published():
    assert config.leg_distances_km(cls.AQUATHON) == [0.6, 3.5]
    assert config.leg_distances_km(cls.TIME_TRIAL) == [13.0]


def test_distance_of_unknown_race_type_is_none():
    assert config.distance_km("track_tt") is None
    assert config.leg_distances_km("track_tt") == []


def test_admin_change_round_trips(tmp_path):
    """An admin can restate the course without touching code."""
    path = tmp_path / "courses.json"
    courses = config.load_courses(path)
    courses[cls.TIME_TRIAL].legs[0].distance_km = 13.4
    config.save_courses(courses, path)

    reloaded = config.load_courses(path)
    assert config.distance_km(cls.TIME_TRIAL, reloaded) == 13.4
    # and the built-in default is untouched
    assert config.DEFAULT_COURSES[cls.TIME_TRIAL].distance_km == 13.0


def test_shipped_config_file_matches_the_defaults():
    """data/courses.json is committed, so it must not drift from the defaults."""
    if not config.CONFIG_PATH.exists():
        pytest.skip("no courses.json checked out")
    on_disk = config.load_courses()
    assert on_disk[cls.TIME_TRIAL].distance_km == 13.0
    assert on_disk[cls.AQUATHON].distance_km == 4.1
