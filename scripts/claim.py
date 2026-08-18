#!/usr/bin/env python
"""Claim your results: search by first name, pick your races, be remembered.

    ctc claim -- James                          # show every matching result
    ctc claim -- James --as "James Carron"      # then pick rows interactively
    ctc claim -- James --as "James Carron" --rows 1,2,5,9
    ctc claim -- --who                          # list everyone already claimed

Claims are recorded against a specific result row (event code + RaceID), never
against a name string, so two people who race under the same first name never
collide. Later events resolve automatically once a spelling has been claimed.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ctc_bot  # noqa: E402,F401
from ctc_bot import identity as idn  # noqa: E402
from ctc_bot import store  # noqa: E402


def _parse_rows(spec: str, count: int) -> list[int]:
    """Turn "1,3,5-8" into zero-based indices, rejecting anything out of range."""
    chosen: set[int] = set()
    for part in spec.replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            start, _, end = part.partition("-")
            chosen.update(range(int(start), int(end) + 1))
        else:
            chosen.add(int(part))

    bad = [n for n in chosen if not 1 <= n <= count]
    if bad:
        raise SystemExit(f"Row(s) out of range: {sorted(bad)} (there are {count}).")
    return sorted(n - 1 for n in chosen)


def show_who(registry: idn.Registry) -> int:
    if not registry.athletes:
        print("Nobody has claimed results yet.")
        return 0
    print(f"{len(registry.athletes)} athlete(s) claimed:\n")
    for athlete in sorted(registry.athletes.values(), key=lambda a: a.display_name):
        variants = ", ".join(sorted(athlete.name_variants))
        print(f"  {athlete.display_name:<24}{len(athlete.claims):>3} race(s)   races as: {variants}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("name", nargs="?", help="first name to search for")
    parser.add_argument("--as", dest="claim_as", help="full name to claim the rows under")
    parser.add_argument("--rows", help="rows to claim, e.g. 1,3,5-8")
    parser.add_argument("--who", action="store_true", help="list everyone already claimed")
    args = parser.parse_args()

    registry = idn.Registry.load()

    if args.who or not args.name:
        return show_who(registry)

    events = store.load_all()
    if not events:
        print("No events stored yet - run `ctc backfill` first.")
        return 1

    candidates = registry.search(args.name, events)
    if not candidates:
        print(f"No results found for {args.name!r} across {len(events)} events.")
        return 1

    print(f"{len(candidates)} result(s) matching {args.name!r} across {len(events)} events:\n")
    for number, candidate in enumerate(candidates, 1):
        owner = ""
        if candidate.claimed_by:
            owner = f"  <- {registry.athletes[candidate.claimed_by].display_name}"
        print(
            f"  [{number:>3}] {(candidate.date_text or candidate.event):<27}"
            f"{candidate.race_type:<11}{candidate.name!r:<18}"
            f"{('#' + str(candidate.position)) if candidate.position else '':>5}"
            f"{candidate.time:>11}{owner}"
        )

    if not args.claim_as:
        print("\nTo claim rows:  ctc claim -- <name> --as \"Your Full Name\" --rows 1,3,5")
        return 0

    if args.rows:
        picked = _parse_rows(args.rows, len(candidates))
    else:
        if not sys.stdin or not sys.stdin.isatty():
            raise SystemExit(
                "\nNo --rows given and no terminal to prompt from.\n"
                'Re-run with e.g.  --rows 1,3,5-8'
            )
        try:
            answer = input("\nWhich rows are yours? (e.g. 1,3,5-8) ").strip()
        except EOFError:
            raise SystemExit("\nCancelled - nothing claimed.") from None
        if not answer:
            print("Nothing selected.")
            return 0
        picked = _parse_rows(answer, len(candidates))

    selected = [candidates[i] for i in picked]
    athlete = registry.claim(args.claim_as, selected)
    registry.save()

    print(f"\nClaimed {len(selected)} race(s) for {athlete.display_name}.")
    print(f"Now races as: {', '.join(sorted(athlete.name_variants))}")
    print(f"Total on record: {len(athlete.claims)} race(s)")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nCancelled - nothing claimed.")
        raise SystemExit(130)
