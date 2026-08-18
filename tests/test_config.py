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
