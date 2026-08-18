"""Course configuration for the club's two race series.

RaceClocker's per-event ``Distance`` field is **not trusted**, for two reasons
found in the club's real history:

* The sample aquathon reports ``8.0 km`` against a real 600 m swim + 3.5 km run
  (4.1 km). Taking it at face value puts every aquathon pace out by ~2x.
* The time trial runs on **two different routes**. Listed distances across 105
  time trials fall into two clean clusters - 13.0-13.1 km (60 events) and
  13.5-14.0 km (44 events) - and the two overlap in 2022-2025, so they are
  alternating courses rather than one course remeasured. Within a cluster the
  small wobble (13.0 vs 13.1) is measurement noise and is flattened to one
  canonical figure.

So each race type carries a canonical distance, and where a type runs on more
than one route, a route is matched by the distance the event advertises. Pace
then uses the route's canonical distance, and trends can mark where an athlete
switched course rather than reading it as a change in form.

Distances are admin-editable via ``data/courses.json``, which overrides these
defaults when present:

    ctc courses              # show the configured courses
    ctc courses -- --set time_trial:Bike=13.4
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .classify import AQUATHON, TIME_TRIAL

CONFIG_PATH = Path(__file__).resolve().parent.parent / "data" / "courses.json"


@dataclass
class Leg:
    """One timed leg of a course."""

    name: str
    distance_km: float


@dataclass
class Route:
    """One course a race type runs on.

    ``min_km``/``max_km`` bracket the distances RaceClocker has advertised for
    this route, so an event is matched to its route by what it claims, not by
    date - the club alternates between routes within the same season.
    """

    name: str
    distance_km: float
    min_km: float
    max_km: float

    def matches(self, listed_km: float | None) -> bool:
        return listed_km is not None and self.min_km <= listed_km <= self.max_km


@dataclass
class Course:
    """The fixed course for a race series.

    Both series are confirmed fixed across a season, so a single course per
    race type is enough; ``legs`` must line up with the number of timed legs
    the parser derives from the results page.
    """

    name: str
    legs: list[Leg] = field(default_factory=list)
    routes: list[Route] = field(default_factory=list)

    @property
    def default_route(self) -> Route | None:
        """The route assumed when an event advertises no distance at all."""
        return self.routes[0] if self.routes else None

    def route_for(self, listed_km: float | None) -> Route | None:
        """The route whose advertised range covers this event.

        An event with **no** advertised distance falls back to the default
        route, so one missing field does not split an athlete's history into a
        separate series. An event advertising a distance that matches no route
        stays unmatched, because that is genuinely odd and worth seeing.
        """
        if listed_km is None:
            return self.default_route
        for route in self.routes:
            if route.matches(listed_km):
                return route
        return None

    @property
    def distance_km(self) -> float:
        return round(sum(leg.distance_km for leg in self.legs), 3)

    @property
    def segments(self) -> int:
        return len(self.legs)


DEFAULT_COURSES: dict[str, Course] = {
    TIME_TRIAL: Course(
        name="Time trial",
        legs=[Leg("Bike", 13.0)],
        routes=[
            Route("Short (13 km)", 13.0, 12.75, 13.25),
            Route("Long (13.8 km)", 13.8, 13.4, 14.25),
        ],
    ),
    AQUATHON: Course(name="Aquathon", legs=[Leg("Swim", 0.6), Leg("Run", 3.5)]),
}


def load_courses(path: Path | None = None) -> dict[str, Course]:
    """Load course config, falling back to the built-in defaults."""
    config_path = path or CONFIG_PATH
    if not config_path.exists():
        # Deep copy: sharing Leg objects would let one caller's edit mutate the
        # module-level defaults for every later load.
        return {
            key: Course(
                course.name,
                [Leg(leg.name, leg.distance_km) for leg in course.legs],
                [Route(r.name, r.distance_km, r.min_km, r.max_km) for r in course.routes],
            )
            for key, course in DEFAULT_COURSES.items()
        }

    payload = json.loads(config_path.read_text(encoding="utf-8"))
    courses: dict[str, Course] = {}
    for race_type, spec in payload.items():
        courses[race_type] = Course(
            name=spec.get("name", race_type),
            legs=[Leg(leg["name"], float(leg["distance_km"])) for leg in spec.get("legs", [])],
            routes=[
                Route(
                    r["name"],
                    float(r["distance_km"]),
                    float(r["min_km"]),
                    float(r["max_km"]),
                )
                for r in spec.get("routes", [])
            ],
        )
    return courses


def save_courses(courses: dict[str, Course], path: Path | None = None) -> Path:
    """Write course config so an admin can edit distances without touching code."""
    config_path = path or CONFIG_PATH
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps({key: asdict(course) for key, course in courses.items()}, indent=2),
        encoding="utf-8",
    )
    return config_path


def course_for(race_type: str, courses: dict[str, Course] | None = None) -> Course | None:
    return (courses or load_courses()).get(race_type)


def distance_km(race_type: str, courses: dict[str, Course] | None = None) -> float | None:
    """The configured distance for a race type.

    This is the authority for every pace and speed calculation. The distance
    printed on an individual event page is deliberately ignored - see the
    module docstring.
    """
    course = course_for(race_type, courses)
    return course.distance_km if course else None


def leg_distances_km(race_type: str, courses: dict[str, Course] | None = None) -> list[float]:
    """Configured distance of each timed leg, in order."""
    course = course_for(race_type, courses)
    return [leg.distance_km for leg in course.legs] if course else []


def route_for_event(
    race_type: str, listed_km: float | None, courses: dict[str, Course] | None = None
) -> Route | None:
    """Which configured route an event ran on, from the distance it advertises."""
    course = course_for(race_type, courses)
    return course.route_for(listed_km) if course else None


def event_distance_km(
    race_type: str, listed_km: float | None = None, courses: dict[str, Course] | None = None
) -> float | None:
    """The distance to use for one event's pace and speed.

    The matched route's canonical distance where the type runs on more than one
    course, otherwise the race type's single configured distance. Never the raw
    figure off the page.
    """
    route = route_for_event(race_type, listed_km, courses)
    if route:
        return route.distance_km
    return distance_km(race_type, courses)
