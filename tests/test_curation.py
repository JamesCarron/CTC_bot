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
        # Excluded by decision: a different discipline, and one-off events.
        ("29th Sept 5k Track TT", "track/running series"),
        ("RUNNING TT", "track/running series"),
        ("30th Sept Track TT 3km (New to Tri)", "track/running series"),
        ("23rd June 3k NtT TT", "track/running series"),
        ("LaGrandeCourse De Cork Tri Club - 40 km Cycle", "one-off club event"),
        ("10k Club race", "one-off club event"),
        ("CTC Dock Beach Training Event", "one-off club event"),
        ("EP-S Away Race A", "one-off club event"),
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
        "CTC TT May 23",
        "Cork tt",
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
    """Genuinely distinct races share dates - only the event itself matters.

    Both of these are real aquathons listed under 20 Jun '24.
    """
    assert curation.assess("CTC Aquathon 20 June", participants=20).include
    assert curation.assess("CTC Aquathon 4 July", participants=19).include


def test_exclusions_always_carry_a_reason():
    """Nothing is dropped silently, so a wrong call can be spotted."""
    for title in ("(Copy of) X", "TEMPLATE", "test race", ""):
        verdict = curation.assess(title, participants=5)
        assert verdict.excluded and verdict.reason


def test_substrings_do_not_trigger_false_positives():
    """Word boundaries matter: "Contest" is not "test", "Demolition" not "demo"."""
    assert curation.assess("Contested Sprint", participants=10).include
    assert curation.assess("Demolition Derby", participants=10).include


def test_field_stat_threshold():
    assert curation.MIN_FIELD_FOR_STATS == 5
