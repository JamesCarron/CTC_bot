"""Course configuration for the club's two race series.

RaceClocker's published ``Distance`` field is **not trusted**. The sample
aquathon reports ``8.0 km``, but the real course is 600 m swim + 3.5 km run
(4.1 km total) - taking the page at face value would put every aquathon pace
out by roughly a factor of two.

Distances therefore live here, admin-editable via ``data/courses.json``, which
overrides these defaults when present.
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
