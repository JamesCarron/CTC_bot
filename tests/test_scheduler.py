"""Tests for the refresh sweep's failure handling.

The sweep's cache is what keeps a nightly refresh from re-fetching 200 events
it already holds, and the thing that makes it dangerous is that "no public code"
and "the request failed" look identical from the outside. Caching the second as
if it were the first hides a genuinely new race for a month.
"""

from pathlib import Path

import pytest

from ctc_bot import discovery, scheduler
from ctc_bot import session as sess


@pytest.fixture
def listings():
    return [
        discovery.Listing(index=1, layout="EventList", title="CTC TT", date_text="12 May '26"),
        discovery.Listing(index=2, layout="EventList", title="CTC TT", date_text="19 May '26"),
    ]


@pytest.fixture
def swept(monkeypatch, tmp_path, listings):
    """Run the sweep against stubbed I/O and hand back the resulting cache.

    Returns a callable taking the ``resolve`` stand-in, so each test supplies
    only the failure it cares about.
    """
    monkeypatch.setattr(discovery, "CACHE_PATH", tmp_path / "listing_cache.json")
    monkeypatch.setattr(sess, "login", lambda *a, **k: object())
    monkeypatch.setattr(discovery, "fetch_listing", lambda **k: listings)

    from ctc_bot import dashboard, store

    monkeypatch.setattr(store, "load_all", lambda *a, **k: [])
    monkeypatch.setattr(store, "save", lambda *a, **k: None)
    monkeypatch.setattr(dashboard, "build_if_stale", lambda *a, **k: None)

    def run(resolve):
        monkeypatch.setattr(discovery, "resolve", resolve)
        summary = scheduler.sweep()
        return summary, discovery.ListingCache.load(tmp_path / "listing_cache.json")

    return run


def test_a_transient_failure_is_not_cached(swept, listings):
    """The bug this guards: a timeout recorded as "never published" would hide a
    real race from every sweep for UNPUBLISHED_RETRY_DAYS."""

    def resolve(listing, **kwargs):
        raise TimeoutError("connection reset")

    summary, cache = swept(resolve)
    assert "2 unresolved" in summary
    for listing in listings:
        assert cache.lookup(listing) == (False, None), "must be retried next sweep"


def test_an_unpublished_event_is_cached(swept, listings):
    """The case the cache exists for: re-checking a dead listing every morning
    is the waste it was built to avoid."""

    def resolve(listing, **kwargs):
        raise LookupError("No public event code found")

    _, cache = swept(resolve)
    for listing in listings:
        assert cache.lookup(listing) == (True, None)


def test_being_signed_out_stops_the_sweep(swept, listings):
    """Every remaining listing would fail identically, so walking the rest just
    hammers RaceClocker with requests that cannot succeed."""
    seen = []

    def resolve(listing, **kwargs):
        seen.append(listing.index)
        raise sess.LoginError("Session expired")

    _, cache = swept(resolve)
    assert seen == [1], "stopped after the first failure"
    for listing in listings:
        assert cache.lookup(listing) == (False, None), "nothing cached on the way out"


def test_a_resolved_event_is_cached_by_code(swept, listings):
    def resolve(listing, **kwargs):
        return discovery.Discovered(listing=listing, code=f"code{listing.index}", html="<html>")

    summary, cache = swept(resolve)
    assert "2 new event(s)" in summary
    assert cache.lookup(listings[0]) == (True, "code1")
