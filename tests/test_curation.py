"""Tests for deciding which events count towards trends.

The cases here are drawn from what the club's real seven-year history actually
contains, not invented ones.
"""

import pytest

from ctc_bot import curation


@pytest.mark.parametrize(
    "title, reason",
    [
        ("(Copy of) CTC Time Trial 15th September 2020", "copy"),
        ("Copy of CTC Aquathon 17 August", "copy"),
        ("(Copy of) Cork tt", "copy"),
        ("Aquathon TEMPLATE COPY ME", "template"),
        ("TT Test Demo", "test"),
        ("test Aquathon", "test"),
        ("My first RaceClocker sample race", "test"),
        ("(Copy of) TEST ONLY!!!", "copy"),
    ],
)
def test_non_races_are_excluded(title, reason):
    verdict = curation.assess(title, participants=12)
    assert verdict.excluded
    assert verdict.reason == reason


@pytest.mark.parametrize(
    "title",
    [
        "Timetrial 18th aug",
        "Aquathon 18th June",
        "CTC Aquathon 4th july",
        "Dock Beach Aquathon 24.06.2021",
        "LaGrandeCourse De Cork Tri Club - 40 km Cycle",
        "10k Club race",
    ],
)
def test_real_races_are_kept(title):
    assert curation.assess(title, participants=15).include


def test_empty_events_are_excluded():
    verdict = curation.assess("CTC TT", participants=0)
    assert verdict.excluded
    assert verdict.reason == "no participants"


def test_untitled_is_excluded():
    assert curation.assess("", participants=10).excluded
    assert curation.assess(None).excluded


def test_default_named_events_are_kept_but_flagged():
    """"New Race" is RaceClocker's default title - plenty are real races."""
    verdict = curation.assess("New Race", participants=15)
    assert verdict.include
    assert "unnamed" in verdict.reason


def test_a_shared_date_is_never_grounds_for_exclusion():
    """Genuinely distinct races share dates - only the title and field matter."""
    assert curation.assess("5k club race", participants=4).include
    assert curation.assess("10k Club race", participants=25).include
    assert curation.assess("LaGrandeCourse De Cork Tri Club - 20 km Cycle", 4).include


def test_exclusions_always_carry_a_reason():
    """Nothing is dropped silently, so a wrong call can be spotted."""
    for title in ("(Copy of) X", "TEMPLATE", "test race", ""):
        verdict = curation.assess(title, participants=5)
        assert verdict.excluded and verdict.reason


def test_substrings_do_not_trigger_false_positives():
    """Word boundaries matter: "Contest" is not "test", "Democracy" not "demo"."""
    assert curation.assess("Club Contest 5k", participants=10).include
    assert curation.assess("Demolition Derby TT", participants=10).include
