#!/usr/bin/env python
"""Show or change the configured course distances.

    ctc courses                                   # show what is configured
    ctc courses -- --set time_trial:Bike=13.4     # change one leg
    ctc courses -- --set aquathon:Swim=0.75 --set aquathon:Run=3.4
    ctc courses -- --reset                        # back to the built-in defaults

These distances are the authority for every pace and speed figure. The figure
printed on an individual RaceClocker event page is never used directly.

The time trial runs on two routes - roughly 13 km and 13.8 km - which the club
alternates between within a season. An event is matched to its route by the
distance it advertises, and that route's canonical distance is used for pace.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ctc_bot  # noqa: E402,F401
from ctc_bot import config  # noqa: E402


def show(courses: dict[str, config.Course]) -> None:
    print(f"Configured courses  ({config.CONFIG_PATH})\n")
    for race_type, course in courses.items():
        legs = ", ".join(f"{leg.name} {leg.distance_km} km" for leg in course.legs)
        print(f"  {race_type:<12}{course.distance_km:>6} km total   {legs}")
        for route in course.routes:
            flag = "shown" if route.enabled else "HIDDEN"
            print(
                f"      route: {route.name:<18}{route.distance_km:>6} km"
                f"   for events listed {route.min_km}-{route.max_km} km   [{flag}]"
            )


def apply_change(courses: dict[str, config.Course], spec: str) -> None:
    """Apply one ``race_type:LegName=distance`` change."""
    try:
        target, _, value = spec.partition("=")
        race_type, _, leg_name = target.partition(":")
        distance = float(value)
    except ValueError:
        raise SystemExit(f"Could not read {spec!r}. Expected e.g. time_trial:Bike=13.4")

    if not (race_type and leg_name and value):
        raise SystemExit(f"Could not read {spec!r}. Expected e.g. time_trial:Bike=13.4")

    course = courses.get(race_type)
    if course is None:
        raise SystemExit(f"Unknown race type {race_type!r}. Known: {', '.join(courses)}")

    for leg in course.legs:
        if leg.name.casefold() == leg_name.casefold():
            if distance <= 0:
                raise SystemExit(f"Distance must be positive, got {distance}")
            print(f"  {race_type}:{leg.name}  {leg.distance_km} km -> {distance} km")
            leg.distance_km = distance
            return

    known = ", ".join(leg.name for leg in course.legs)
    raise SystemExit(f"Unknown leg {leg_name!r} for {race_type}. Known: {known}")


def set_enabled(courses: dict[str, config.Course], spec: str, enabled: bool) -> None:
    """Show or hide one route, by ``race_type:RouteName``.

    Hiding never deletes anything: the events stay stored and correctly
    attributed, so re-enabling restores the history with no refetch.
    """
    race_type, _, route_name = spec.partition(":")
    if not (race_type and route_name):
        raise SystemExit(f"Could not read {spec!r}. Expected e.g. time_trial:Long (13.8 km)")

    course = courses.get(race_type)
    if course is None:
        raise SystemExit(f"Unknown race type {race_type!r}. Known: {', '.join(courses)}")
    if not course.routes:
        raise SystemExit(f"{race_type} has no routes to show or hide.")

    for route in course.routes:
        if route.name.casefold() == route_name.casefold():
            was = "shown" if route.enabled else "hidden"
            now = "shown" if enabled else "hidden"
            print(f"  {race_type}:{route.name}  {was} -> {now}")
            route.enabled = enabled
            return

    known = ", ".join(r.name for r in course.routes)
    raise SystemExit(f"Unknown route {route_name!r} for {race_type}. Known: {known}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--set", dest="changes", action="append", metavar="race_type:Leg=km",
        help="change one leg's distance; may be repeated",
    )
    parser.add_argument(
        "--enable", dest="enable", action="append", metavar="race_type:Route",
        help="show a route's tab in the dashboard; may be repeated",
    )
    parser.add_argument(
        "--disable", dest="disable", action="append", metavar="race_type:Route",
        help="hide a route's tab (its events stay stored); may be repeated",
    )
    parser.add_argument("--reset", action="store_true", help="restore the built-in defaults")
    args = parser.parse_args()

    if args.reset:
        if config.CONFIG_PATH.exists():
            config.CONFIG_PATH.unlink()
        courses = config.load_courses()
        config.save_courses(courses)
        print("Reset to the built-in defaults.\n")
        show(courses)
        return 0

    courses = config.load_courses()

    if args.enable or args.disable:
        print("Applying changes:")
        for spec in args.enable or []:
            set_enabled(courses, spec, True)
        for spec in args.disable or []:
            set_enabled(courses, spec, False)
        config.save_courses(courses)
        print()
        show(courses)
        print("\nRebuild the dashboard to see this: ctc build")
        return 0

    if not args.changes:
        show(courses)
        print("\nTo change one:  ctc courses -- --set time_trial:Bike=13.4")
        return 0

    print("Applying changes:")
    for spec in args.changes:
        apply_change(courses, spec)

    config.save_courses(courses)
    print()
    show(courses)
    print("\nPace and speed figures will use these on the next dashboard build.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
