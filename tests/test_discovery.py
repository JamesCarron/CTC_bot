"""Tests for parsing the admin event listing.

Pinned to a trimmed but verbatim sample of ``My_Events.php`` covering both
layouts RaceClocker uses for the same data, a locked event, and the oldest
entry in the account.
"""

from pathlib import Path

import pytest

from ctc_bot import classify as cls
from ctc_bot import discovery
from ctc_bot import raceclocker as rc

FIXTURE = Path(__file__).parent / "fixtures_my_events.html"


@pytest.fixture(scope="module")
def listings():
    return discovery.parse_listing(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def by_index(listings):
    return {listing.index: listing for listing in listings}


def test_parses_both_layouts(listings):
    assert len(listings) == 4
    assert {l.layout for l in listings} == {"EventCard", "EventList"}


def test_every_entry_has_a_title_and_date(listings):
    assert all(l.title for l in listings)
    assert all(l.date_text for l in listings)


def test_card_layout_fields(by_index):
    tt = by_index[0]
    assert tt.title == "Timetrial 18th aug"
    assert tt.date_text.startswith("18 Aug '26")
    assert tt.distance_km == 13.0
    assert tt.participants == 15
    assert tt.locked is False


def test_list_layout_fields(by_index):
    old = by_index[227]
    assert old.title == "CTC Aquathon 4th july"
    assert old.date_text == "4 Jul '19"
    assert old.participants == 33
    assert old.layout == "EventList"


def test_locked_events_are_flagged(by_index):
    """Locked events are still reachable through the admin result page."""
    assert by_index[13].locked is True
    assert by_index[0].locked is False


def test_start_type_gives_the_split_structure(by_index):
    """The listing states the structure outright, so it need not be inferred.

    A time trial has no intermediate split (one timed leg); an aquathon has
    one (two timed legs).
    """
    assert by_index[0].start_type.strip() == "Time trial"
    assert by_index[0].intermediate_splits == 0
    assert by_index[0].mass_start is False

    assert by_index[13].start_type == "Mass start (1 split)"
    assert by_index[13].intermediate_splits == 1
    assert by_index[13].mass_start is True


def test_listing_distance_matches_the_published_figure(by_index):
    """Confirms the listing repeats the page's wrong aquathon distance.

    8.0 km against a real 4.1 km course - so this field is captured for
    reference but never used for pace. See ctc_bot.config.
    """
    assert by_index[13].distance_km == 8.0


def test_fields_do_not_bleed_between_entries(by_index):
    """Entries sit adjacent in the markup, so chunking must be exact.

    The metadata div for one event directly precedes the next event's opening
    div; a sloppy split would attribute a time trial's distance to an aquathon.
    """
    assert by_index[23].title == "Timetrail 12th may"
    assert by_index[23].distance_km == 13.0
    assert by_index[227].distance_km == 5.0


def test_result_url_uses_the_index(by_index):
    assert by_index[13].result_url.endswith("Event_Result.php?index=13")


@pytest.mark.parametrize(
    "metadata, expected",
    [("13 km Cycling time trial", 13.0), ("8 km Other", 8.0), ("", None), (None, None)],
)
def test_distance_parsing(metadata, expected):
    assert discovery.Listing(index=0, layout="EventCard", metadata=metadata).distance_km == expected


@pytest.mark.parametrize(
    "start_type, splits, mass",
    [
        ("Time trial ", 0, False),
        ("Mass start (1 split)", 1, True),
        ("Mass start (2 splits)", 2, True),
        (None, None, None),
    ],
)
def test_split_and_start_parsing(start_type, splits, mass):
    listing = discovery.Listing(index=0, layout="EventCard", start_type=start_type)
    assert listing.intermediate_splits == splits
    assert listing.mass_start is mass


# ---- the admin console's stated race format ------------------------------


def test_listing_settles_classification_outright():
    """The console states the start type, so it need not be inferred."""
    assert cls.from_listing({"intermediate_splits": 1}) == cls.AQUATHON
    assert cls.from_listing({"intermediate_splits": 0}) == cls.TIME_TRIAL
    assert cls.from_listing({"start_type": "Mass start (1 split)"}) == cls.AQUATHON
    assert cls.from_listing({"start_type": "Time trial "}) == cls.TIME_TRIAL
    assert cls.from_listing({}) is None
    assert cls.from_listing(None) is None


def test_listing_overrides_a_misleading_weekday():
    """A Tuesday aquathon is still an aquathon.

    Real case: an event titled "Aquathon" ran on Tuesday 10 Mar '26. Weekday
    said time trial, structure said aquathon, so inference alone deadlocked at
    `unknown`. The console states "Mass start (1 split)", which settles it.
    """
    event = rc.Event(
        code="tues0001",
        title="Aquathon",
        date_text="Tuesday 10 Mar '26, 15:00",
        distance=4.0,
        distance_unit="km",
        results=[
            {"TmSplit1": "15:00:00", "TmSplit2": "15:12:00", "TmSplit5": "15:30:00"}
        ],
    )
    assert cls.classify(event).race_type == cls.UNKNOWN  # inference deadlocks

    decided = cls.classify(event, {"start_type": "Mass start (1 split)", "intermediate_splits": 1})
    assert decided.race_type == cls.AQUATHON
    assert decided.confident
    assert any("admin console says" in note for note in decided.notes)


def test_listing_agreement_records_no_dissent():
    event = rc.Event(
        code="thur0001",
        title="Aquathon 18th June",
        date_text="Thursday 18 Jun '26, 19:00",
        distance=4.0,
        distance_unit="km",
        results=[
            {"TmSplit1": "19:00:00", "TmSplit2": "19:12:00", "TmSplit5": "19:30:00"}
        ],
    )
    decided = cls.classify(event, {"intermediate_splits": 1})
    assert decided.race_type == cls.AQUATHON
    assert decided.notes == []


# ---- the listing cache ---------------------------------------------------


def _listing(index, title, date_text):
    return discovery.Listing(index=index, layout="EventList", title=title, date_text=date_text)


def test_cache_key_ignores_the_index():
    """The index is a position in the account's list and shifts constantly."""
    a = _listing(3, "CTC TT", "12 May '26")
    b = _listing(97, "CTC TT", "12 May '26")
    assert discovery.ListingCache.key(a) == discovery.ListingCache.key(b)


def test_cache_key_normalises_title_whitespace_and_case():
    a = _listing(1, "Cork  TT ", "12 May '26")
    b = _listing(1, "cork tt", "12 May '26")
    assert discovery.ListingCache.key(a) == discovery.ListingCache.key(b)


def test_cache_key_separates_the_same_title_on_different_dates():
    a = _listing(1, "Cork tt", "12 May '26")
    b = _listing(2, "Cork tt", "19 May '26")
    assert discovery.ListingCache.key(a) != discovery.ListingCache.key(b)


def test_cache_returns_a_recorded_code():
    cache = discovery.ListingCache()
    listing = _listing(1, "CTC TT", "12 May '26")
    cache.record(listing, "abc12345")
    assert cache.lookup(listing) == (True, "abc12345")


def test_cache_remembers_an_unpublished_event():
    """19 events have no public code; re-checking them every sweep is the waste."""
    cache = discovery.ListingCache()
    listing = _listing(1, "New Race", "12 May '26")
    cache.record(listing, None)
    assert cache.lookup(listing) == (True, None)


def test_unpublished_entries_are_retried_eventually():
    """An event can be published later, so "no code" must not be permanent."""
    from datetime import datetime, timedelta

    cache = discovery.ListingCache()
    listing = _listing(1, "New Race", "12 May '26")
    cache.record(listing, None)
    stale = datetime.now() - timedelta(days=discovery.UNPUBLISHED_RETRY_DAYS + 1)
    cache.entries[discovery.ListingCache.key(listing)]["checked"] = stale.isoformat()
    assert cache.lookup(listing) == (False, None)


def test_a_code_entry_never_expires():
    """A resolved code is durable; only the "not published" answer can change."""
    from datetime import datetime, timedelta

    cache = discovery.ListingCache()
    listing = _listing(1, "CTC TT", "12 May '26")
    cache.record(listing, "abc12345")
    ancient = datetime.now() - timedelta(days=3650)
    cache.entries[discovery.ListingCache.key(listing)]["checked"] = ancient.isoformat()
    assert cache.lookup(listing) == (True, "abc12345")


def test_a_miss_is_reported_as_a_miss():
    cache = discovery.ListingCache()
    assert cache.lookup(_listing(1, "Unseen", "12 May '26")) == (False, None)


def test_duplicate_title_and_date_is_flagged_ambiguous():
    """Two events sharing a title and date exist in this account.

    Guessing between them would attach one event's results to the other, so the
    sweep resolves both properly instead of trusting the cache.
    """
    listings = [
        _listing(1, "Cork tt", "12 May '26"),
        _listing(2, "Cork tt", "12 May '26"),
        _listing(3, "CTC TT", "19 May '26"),
    ]
    ambiguous = discovery.ListingCache().ambiguous_keys(listings)
    assert discovery.ListingCache.key(listings[0]) in ambiguous
    assert discovery.ListingCache.key(listings[2]) not in ambiguous


def test_cache_round_trips_through_disk(tmp_path):
    path = tmp_path / "listing_cache.json"
    listing = _listing(1, "CTC TT", "12 May '26")
    cache = discovery.ListingCache()
    cache.record(listing, "abc12345")
    cache.save(path)
    assert discovery.ListingCache.load(path).lookup(listing) == (True, "abc12345")


def test_a_corrupt_cache_is_ignored_rather_than_fatal(tmp_path):
    """Losing the cache costs one slow sweep; it must never cost correctness."""
    path = tmp_path / "listing_cache.json"
    path.write_text("{ not json", encoding="utf-8")
    assert discovery.ListingCache.load(path).entries == {}


def test_missing_cache_is_empty(tmp_path):
    assert discovery.ListingCache.load(tmp_path / "nothing.json").entries == {}
