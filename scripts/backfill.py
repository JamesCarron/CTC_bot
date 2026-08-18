#!/usr/bin/env python
"""Pull every event from the RaceClocker admin console into the local store.

Run:

    ctc backfill                # fetch anything not already stored
    ctc backfill -- --limit 5   # try just a few first
    ctc backfill -- --refetch   # re-download events already stored

Events already stored are skipped, so this is safe to re-run - it becomes the
scheduled Wednesday/Friday refresh. Requests are spaced out, because this walks
the club's entire history.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ctc_bot  # noqa: E402,F401
from ctc_bot import discovery, session, store  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--limit", type=int, help="stop after this many new events")
    parser.add_argument("--delay", type=float, default=0.5, help="seconds between requests")
    parser.add_argument("--refetch", action="store_true", help="re-download stored events")
    args = parser.parse_args()

    print("Logging in...")
    http = session.login()

    print("Reading the admin event list...")
    listings = discovery.fetch_listing(session=http)
    print(f"  {len(listings)} events listed\n")

    already = {stored.code for stored in store.load_all()} if not args.refetch else set()
    if already:
        print(f"{len(already)} already stored - skipping those\n")

    added = skipped = 0
    failures: list[tuple[discovery.Listing, str]] = []

    for position, listing in enumerate(listings, 1):
        if args.limit is not None and added >= args.limit:
            print(f"\nReached --limit {args.limit}.")
            break

        label = f"[{position:>3}/{len(listings)}] {(listing.title or '?')[:34]:<36}"
        try:
            found = discovery.resolve(listing, session=http)
        except Exception as exc:
            failures.append((listing, str(exc)))
            print(f"{label} FAILED  {str(exc)[:60]}")
            continue

        if found.code in already:
            skipped += 1
            continue

        extra = {
            "index": listing.index,
            "start_type": listing.start_type,
            "intermediate_splits": listing.intermediate_splits,
            "listed_distance_km": listing.distance_km,
            "participants": listing.participants,
            "locked": listing.locked,
        }
        stored = store.save(found.html, found.code, extra=extra)
        added += 1
        flag = "" if stored.confident else "  <- needs review"
        print(
            f"{label} {found.code}  {stored.race_type:<11}"
            f"{len(stored.event.results):>3} athletes{flag}"
        )

        if args.delay:
            time.sleep(args.delay)

    print()
    print("=" * 64)
    print(f"added {added}   skipped {skipped}   failed {len(failures)}")
    if failures:
        print("\nFailures (usually events never published to a public link):")
        for listing, reason in failures[:15]:
            print(f"  idx {listing.index:<4} {(listing.title or '?')[:32]:<34} {reason[:50]}")
        if len(failures) > 15:
            print(f"  ... and {len(failures) - 15} more")
    print("=" * 64)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInterrupted - already-stored events are kept.")
        raise SystemExit(130)
