#!/usr/bin/env python
"""List the events stored locally, with their results.

Run:  pixi run events
      pixi run events -- 7eecd645     # full field for one event
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ctc_bot  # noqa: E402,F401
from ctc_bot import config, identity as idn, raceclocker as rc, store  # noqa: E402


def show_summary() -> int:
    events = store.load_all()
    if not events:
        print("No events stored yet.")
        return 1

    print(f"{len(events)} event(s) stored\n")
    print(f"{'code':<10}{'date':<28}{'type':<12}{'title':<24}{'athletes':>9}")
    print("-" * 83)
    for stored in events:
        flag = "" if stored.confident else "  (needs review)"
        print(
            f"{stored.code:<10}{stored.date_text or '?':<28}{stored.race_type:<12}"
            f"{(stored.title or '?'):<24}{len(stored.event.results):>9}{flag}"
        )
    return 0


def show_event(code: str) -> int:
    events = [s for s in store.load_all() if s.code == code]
    if not events:
        print(f"No stored event with code {code!r}.")
        return 1

    stored = events[0]
    course = config.course_for(stored.race_type)
    leg_names = [leg.name for leg in course.legs] if course else []

    print(f"{stored.title}  ({stored.date_text})")
    print(f"{stored.race_type}", end="")
    if course:
        print(f" - {course.distance_km} km: " + ", ".join(f"{l.name} {l.distance_km}km" for l in course.legs))
    else:
        print()
    print()

    header = f"{'pos':>4}  {'name':<14}{'bib':>4}{'time':>12}"
    for name in leg_names:
        header += f"{name:>11}"
    print(header)
    print("-" * len(header))

    for row in rc.ranked(stored.event.results):
        line = (
            f"{row['Position']:>4}  {row['Name'].strip():<14}{row['Bib']:>4}"
            f"{idn.format_time(float(row['TmResultSec'])):>12}"
        )
        for leg in rc.leg_seconds(row):
            line += f"{idn.format_time(leg):>11}"
        print(line)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("code", nargs="?", help="event code, e.g. 7eecd645")
    args = parser.parse_args()

    return show_event(args.code) if args.code else show_summary()


if __name__ == "__main__":
    raise SystemExit(main())
