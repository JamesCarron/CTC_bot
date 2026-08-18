"""Course configuration for the club's two race series.

RaceClocker's per-event ``Distance`` field is **not trusted**, for two reasons
found in the club's real history:

* The sample aquathon reports ``8.0 km`` against a real 600 m swim + 3.5 km run
  (4.1 km). Taking it at face value puts every aquathon pace out by ~2x.
* Across 105 time trials the listed distance drifts between 13.0 and 14.0 km
  (13.0, 13.1, 13.5, 13.6, 13.62, 13.8, 13.9, 14.0) - almost certainly the same
  road measured by different devices over seven years, not eight courses. Left
  alone it would inject up to 7% of phantom variation into every pace trend,
  larger than most athletes' year-on-year improvement.

So one distance is configured per race type and used for **every** event of
that type. The club time trial is 13 km by decision.

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
class Course:
    """The fixed course for a race series.

    Both series are confirmed fixed across a season, so a single course per
    race type is enough; ``legs`` must line up with the number of timed legs
    the parser derives from the results page.
    """

    name: str
    legs: list[Leg] = field(default_factory=list)

    @property
    def distance_km(self) -> float:
        return round(sum(leg.distance_km for leg in self.legs), 3)

    @property
    def segments(self) -> int:
        return len(self.legs)


DEFAULT_COURSES: dict[str, Course] = {
    TIME_TRIAL: Course(name="Time trial", legs=[Leg("Bike", 13.0)]),
    AQUATHON: Course(name="Aquathon", legs=[Leg("Swim", 0.6), Leg("Run", 3.5)]),
}


def load_courses(path: Path | None = None) -> dict[str, Course]:
    """Load course config, falling back to the built-in defaults."""
    config_path = path or CONFIG_PATH
    if not config_path.exists():
        # Deep copy: sharing Leg objects would let one caller's edit mutate the
        # module-level defaults for every later load.
        return {
            key: Course(course.name, [Leg(leg.name, leg.distance_km) for leg in course.legs])
            for key, course in DEFAULT_COURSES.items()
        }

    payload = json.loads(config_path.read_text(encoding="utf-8"))
    courses: dict[str, Course] = {}
    for race_type, spec in payload.items():
        courses[race_type] = Course(
            name=spec.get("name", race_type),
            legs=[Leg(leg["name"], float(leg["distance_km"])) for leg in spec.get("legs", [])],
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
