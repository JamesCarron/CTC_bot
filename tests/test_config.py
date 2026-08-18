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


def test_page_distance_is_never_used_directly():
    """Eight distinct figures appear across the club's 105 time trials.

    Using them as published would score the same 25-minute ride eight different
    ways. Resolving through routes collapses them to the two real courses.
    """
    listed_across_history = [13.0, 13.1, 13.5, 13.6, 13.62, 13.8, 13.9, 14.0]
    seconds = 1500.0  # same rider, same effort, every time

    speeds_from_page = {
        round(listed / (seconds / 3600), 4) for listed in listed_across_history
    }
    speeds_from_routes = {
        round(config.event_distance_km(cls.TIME_TRIAL, listed) / (seconds / 3600), 4)
        for listed in listed_across_history
    }

    assert len(speeds_from_page) == 8  # eight answers to one question
    assert len(speeds_from_routes) == 2  # one per real course
    assert max(speeds_from_page) - min(speeds_from_page) > 2.0  # km/h of noise removed


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


# ---- routes: the time trial runs on two courses --------------------------


def test_time_trial_has_two_routes():
    """Listed distances fall into two clusters that overlap in 2022-2025.

    They are alternating courses, not one course remeasured, so an event is
    matched to its route by the distance it advertises rather than by date.
    """
    routes = config.load_courses()[cls.TIME_TRIAL].routes
    assert [r.distance_km for r in routes] == [13.0, 13.8]


@pytest.mark.parametrize(
    "listed, expected",
    [
        (13.0, 13.0), (13.1, 13.0),                       # short-course cluster
        (13.5, 13.8), (13.6, 13.8), (13.62, 13.8),        # long-course cluster
        (13.8, 13.8), (13.9, 13.8), (14.0, 13.8),
    ],
)
def test_every_listed_distance_maps_to_a_route(listed, expected):
    assert config.event_distance_km(cls.TIME_TRIAL, listed) == expected


def test_unlisted_event_falls_back_to_the_course_distance():
    """One 2026 time trial advertises no distance at all."""
    assert config.event_distance_km(cls.TIME_TRIAL, None) == 13.0


def test_wobble_within_a_route_is_flattened():
    """13.0 and 13.1 are the same road; they must not score different speeds."""
    assert config.event_distance_km(cls.TIME_TRIAL, 13.0) == config.event_distance_km(
        cls.TIME_TRIAL, 13.1
    )


def test_the_two_routes_are_kept_apart():
    """A 6% difference in course length is real and must not be flattened."""
    short = config.event_distance_km(cls.TIME_TRIAL, 13.0)
    long = config.event_distance_km(cls.TIME_TRIAL, 13.8)
    assert short != long
    assert round((long - short) / short * 100) == 6


def test_aquathon_has_no_routes():
    """Only the time trial alternates courses."""
    assert config.load_courses()[cls.AQUATHON].routes == []
    assert config.event_distance_km(cls.AQUATHON, 8.0) == 4.1


def test_routes_survive_a_config_round_trip(tmp_path):
    path = tmp_path / "courses.json"
    config.save_courses(config.load_courses(), path)
    reloaded = config.load_courses(path)
    assert config.event_distance_km(cls.TIME_TRIAL, 13.8, reloaded) == 13.8
