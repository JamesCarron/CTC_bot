"""Turn stored events into per-athlete performance series.

Everything the dashboard draws is computed here, so the rendering layer holds no
statistics of its own.

Four decisions from reviewing the club's real history shape this module:

* **Speed uses the route's distance**, never the figure on the event page. The
  time trial alternates between a ~13 km and a ~13.8 km course, and the pages
  disagree with themselves within each.
* **Field statistics need a field.** A z-score or percentile from a two-person
  race is noise dressed as signal, so they are withheld below
  ``curation.MIN_FIELD_FOR_STATS`` finishers - the raw time and speed still show.
* **A trend line needs races.** A fitted direction from two points is drawing a
  line through noise; ``identity.MIN_RACES_FOR_TREND`` gates it.
* **Placeholder entries still count towards the field**, because they really did
  race, but never become an athlete.
* **Implausible results are dropped entirely.** Club timing is done by hand and
  misfires both ways - a start and stop pressed together produced a 7-second
  "time trial" at 7,097 km/h, and a timer left running produced a 24-hour
  aquathon. Such a row is excluded from the athlete's history *and* from the
  field mean, since one 7,097 km/h entry would wreck every z-score in that race.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from datetime import date, timedelta

from . import config, curation
from . import identity as idn
from . import overrides as ovr
from . import raceclocker as rc

_MONTHS = {
    m: i
    for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1
    )
}

# "Thursday 16 Jul '26, 19:00" - the format every stored event uses.
_DATE_RE = re.compile(r"(\d{1,2})\s+(\w{3})\w*\s+'(\d{2})")


def parse_date(date_text: str | None) -> date | None:
    """Read the event date out of RaceClocker's header text."""
    if not date_text:
        return None
    match = _DATE_RE.search(date_text)
    if not match:
        return None
    day, month_name, year = match.groups()
    month = _MONTHS.get(month_name[:3].lower())
    if not month:
        return None
    try:
        return date(2000 + int(year), month, int(day))
    except ValueError:
        return None


def season_of(when: date) -> int:
    """Calendar year, used only to colour a continuous trend line by season."""
    return when.year


@dataclass
class Performance:
    """One athlete's result in one race."""

    athlete_id: str
    display_name: str
    verified: bool
    event_code: str
    race_id: str
    event_title: str | None
    when: date
    race_type: str
    route: str | None
    distance_km: float | None
    seconds: float
    position: int
    field_size: int
    source: str = ""
    contested: bool = False
    edited: bool = False
    manual: bool = False
    original_seconds: float | None = None
    addition_id: str | None = None
    leg_seconds: list[float] = field(default_factory=list)
    z_score: float | None = None
    percentile: float | None = None
    is_personal_best: bool = False

    @property
    def speed_kmh(self) -> float | None:
        if not self.distance_km:
            return None
        return self.distance_km / (self.seconds / 3600)

    @property
    def pace_min_per_km(self) -> float | None:
        if not self.distance_km:
            return None
        return (self.seconds / 60) / self.distance_km

    @property
    def series(self) -> str:
        """The group a performance may be trended within.

        Times are only comparable inside one race type *and* one route, so the
        two are combined into a single series key.
        """
        return f"{self.race_type}|{self.route}" if self.route else self.race_type


@dataclass
class Athlete:
    """One person's whole history."""

    athlete_id: str
    display_name: str
    verified: bool
    contested: bool = False
    performances: list[Performance] = field(default_factory=list)

    @property
    def race_count(self) -> int:
        return len(self.performances)

    @property
    def first_raced(self) -> date:
        return min(p.when for p in self.performances)

    @property
    def last_raced(self) -> date:
        return max(p.when for p in self.performances)

    def in_series(self, series: str) -> list[Performance]:
        return sorted(
            (p for p in self.performances if p.series == series), key=lambda p: p.when
        )

    @property
    def series_keys(self) -> list[str]:
        return sorted({p.series for p in self.performances})

    def can_trend(self, series: str) -> bool:
        return len(self.in_series(series)) >= idn.MIN_RACES_FOR_TREND

    def trend(self, series: str) -> tuple[float, float] | None:
        """Least-squares fit of speed against time, as (slope_per_year, r2).

        Fitted on speed rather than finish time so that improvement always reads
        as a positive slope, and so the two routes stay comparable.
        """
        points = [
            (p.when.toordinal(), p.speed_kmh)
            for p in self.in_series(series)
            if p.speed_kmh is not None
        ]
        if len(points) < idn.MIN_RACES_FOR_TREND:
            return None

        xs = [x for x, _ in points]
        ys = [y for _, y in points]
        mean_x, mean_y = statistics.fmean(xs), statistics.fmean(ys)
        sxx = sum((x - mean_x) ** 2 for x in xs)
        if sxx == 0:
            return None

        slope = sum((x - mean_x) * (y - mean_y) for x, y in points) / sxx
        intercept = mean_y - slope * mean_x
        ss_res = sum((y - (slope * x + intercept)) ** 2 for x, y in points)
        ss_tot = sum((y - mean_y) ** 2 for y in ys)
        r2 = 1 - ss_res / ss_tot if ss_tot else 0.0
        return slope * 365.25, r2


def _event_finishers(stored, courses=None) -> list[dict]:
    """Plausible rows with a real time, ranked - placeholders included.

    Placeholders raced, so they belong in the field a z-score is measured
    against, even though they never become an athlete. Implausibly fast or slow
    rows do not: they are timing errors, and leaving one in would distort the
    field mean for everyone else in that race.

    Positions are recomputed after filtering, so a dropped row does not leave a
    gap in the finishing order.
    """
    distance = config.event_distance_km(
        stored.race_type, stored.listing.get("listed_distance_km"), courses
    )
    plausible = [
        row
        for row in stored.event.results
        if _row_seconds(row) is not None
        and config.is_plausible(
            stored.race_type,
            _row_seconds(row),
            distance,
            courses,
            leg_seconds=rc.leg_seconds(row),
        )
    ]
    return rc.ranked(plausible)


def _row_seconds(row: dict) -> float | None:
    try:
        seconds = float(row.get("TmResultSec"))
    except (TypeError, ValueError):
        return None
    return seconds if seconds > 0 else None


def build(
    stored_events, registry: idn.Registry, corrections: "ovr.Overrides | None" = None
) -> dict[str, Athlete]:
    """Compute every athlete's performance series from curated events.

    Corrections are applied *before* anything is computed, so a corrected time
    is reflected in the field mean, the finishing order and every z-score in
    that race - a correction that left everyone else measured against the old
    mean would be worse than none.
    """
    corrections = corrections if corrections is not None else ovr.Overrides.load()
    stored_events = corrections.apply_times(stored_events)
    included, _ = curation.partition(stored_events)
    resolutions = idn.resolve(included, registry)
    courses = config.load_courses()

    athletes: dict[str, Athlete] = {}

    for stored in included:
        when = parse_date(stored.date_text)
        if when is None:
            continue

        finishers = _event_finishers(stored, courses)
        field_size = len(finishers)
        times = [float(row["TmResultSec"]) for row in finishers]

        # Field statistics only where the field is big enough to mean anything.
        reliable = field_size >= curation.MIN_FIELD_FOR_STATS
        mean_time = statistics.fmean(times) if reliable else None
        stdev_time = statistics.pstdev(times) if reliable and len(times) > 1 else None

        listed = stored.listing.get("listed_distance_km")
        route = config.route_for_event(stored.race_type, listed, courses)
        distance = config.event_distance_km(stored.race_type, listed, courses)

        for row in finishers:
            key = (stored.code, str(row["RaceID"]))
            resolution = resolutions.get(key)
            if resolution is None or not resolution.is_athlete or not resolution.athlete_id:
                continue

            seconds = float(row["TmResultSec"])
            performance = Performance(
                athlete_id=resolution.athlete_id,
                display_name=resolution.display_name,
                verified=resolution.verified,
                event_code=stored.code,
                race_id=str(row["RaceID"]),
                event_title=stored.title,
                when=when,
                race_type=stored.race_type,
                route=route.name if route else None,
                distance_km=distance,
                seconds=seconds,
                position=row["Position"],
                field_size=field_size,
                source=resolution.source,
                contested=resolution.may_be_several_people,
                edited=bool(row.get("_edited")),
                original_seconds=(
                    edit.original_seconds
                    if (edit := corrections.edit_for(stored.code, row["RaceID"]))
                    else None
                ),
                leg_seconds=rc.leg_seconds(row),
            )

            if stdev_time:
                # Negative is faster than the field, which reads as better.
                performance.z_score = (seconds - mean_time) / stdev_time
            if reliable and field_size > 1:
                performance.percentile = 100 * (field_size - row["Position"]) / (field_size - 1)

            athlete = athletes.get(resolution.athlete_id)
            if athlete is None:
                athlete = Athlete(
                    athlete_id=resolution.athlete_id,
                    display_name=resolution.display_name,
                    verified=resolution.verified,
                )
                athletes[resolution.athlete_id] = athlete
            # A claim anywhere makes the whole identity verified.
            athlete.verified = athlete.verified or resolution.verified
            athlete.contested = athlete.contested or resolution.may_be_several_people
            athlete.performances.append(performance)

    _add_manual_results(athletes, corrections, courses)
    _mark_personal_bests(athletes)
    return athletes


def _add_manual_results(athletes, corrections, courses) -> None:
    """Fold in races that the timing system never recorded.

    A hand-added result has no field, so it carries no position, z-score or
    percentile - there is nothing to have been measured against. It still counts
    towards the athlete's own history and trend, and is marked everywhere.
    """
    for addition in corrections.additions:
        athlete = athletes.get(addition.athlete_id)
        if athlete is None:
            continue  # the athlete was released or renamed since
        when = date.fromisoformat(addition.when)
        route = addition.route
        if route is None:
            course = config.course_for(addition.race_type, courses)
            default = course.default_route if course else None
            route = default.name if default else None
        athlete.performances.append(
            Performance(
                athlete_id=athlete.athlete_id,
                display_name=athlete.display_name,
                verified=athlete.verified,
                event_code=addition.event_code,
                race_id=addition.id,
                event_title=addition.title,
                when=when,
                race_type=addition.race_type,
                route=route,
                distance_km=config.event_distance_km(addition.race_type, None, courses),
                seconds=addition.seconds,
                position=0,
                field_size=0,
                manual=True,
                addition_id=addition.id,
            )
        )


def _mark_personal_bests(athletes: dict[str, Athlete]) -> None:
    """Flag each athlete's fastest run within every comparable series."""
    for athlete in athletes.values():
        for series in athlete.series_keys:
            runs = athlete.in_series(series)
            best = min(runs, key=lambda p: p.seconds)
            best.is_personal_best = True


# --- how hard was tonight? ---------------------------------------------------

CONDITIONS_WINDOW_DAYS = 60
"""How far either side of a race to look for an athlete's own baseline.

Deliberately *not* their whole history. Baselining on everything an athlete has
ever done measures their form as much as the evening: a club that is fitter in
August than in March would make every August look like fast conditions, for
ever. A window of a couple of months either side is short enough that fitness is
roughly flat across it and long enough that a weekly race fills it comfortably -
in practice 7 to 12 people qualify on a normal club night.
"""

CONDITIONS_MIN_BASELINE = idn.MIN_RACES_FOR_TREND
"""Nearby races an athlete needs before their own median means anything."""

CONDITIONS_MIN_REGULARS = 5
"""Below this, say nothing.

With four people the figure is one person's bad day wearing a lab coat. The
honest output for a thin field is no output at all.
"""


@dataclass
class Conditions:
    """How fast a race ran, measured against the people who were there.

    The field mean is useless for this: who turns up changes week to week, so a
    night with three fast riders absent looks like bad weather. Instead every
    regular is compared with *their own* recent median, and the ratios are
    combined - which asks "was everybody slower than they usually are?", the
    question the weather actually answers.
    """

    ratio: float
    """Median of speed-vs-own-baseline across the regulars. >1 is faster."""

    regulars: int

    @property
    def percent(self) -> float:
        """Signed percentage, positive for faster than usual."""
        return (self.ratio - 1) * 100

    @property
    def verdict(self) -> str:
        """A plain description of the evening.

        The thresholds come from the spread actually observed: across recent
        club time trials the figure ranges about -3% to +4%, so a 5% band would
        never fire and a 0.5% one would fire every week.
        """
        pct = self.percent
        if pct >= 2.5:
            return "fast"
        if pct >= 1.0:
            return "quick"
        if pct > -1.0:
            return "typical"
        if pct > -2.5:
            return "slow"
        return "hard"


def race_conditions(
    athletes: dict[str, Athlete], series: str, event_code: str
) -> Conditions | None:
    """How this race compared with how its regulars normally go.

    Returns ``None`` rather than a guess whenever too few people have a usable
    baseline - a thin night genuinely cannot be judged.
    """
    ratios = []
    for athlete in athletes.values():
        runs = athlete.in_series(series)
        tonight = next((p for p in runs if p.event_code == event_code), None)
        if tonight is None or not tonight.speed_kmh:
            continue

        first = tonight.when - timedelta(days=CONDITIONS_WINDOW_DAYS)
        last = tonight.when + timedelta(days=CONDITIONS_WINDOW_DAYS)
        baseline = [
            p.speed_kmh
            for p in runs
            if p.event_code != event_code and p.speed_kmh and first <= p.when <= last
        ]
        if len(baseline) < CONDITIONS_MIN_BASELINE:
            continue
        # Median, not mean: one shocker in the window must not move the very
        # yardstick it is being measured against.
        ratios.append(tonight.speed_kmh / statistics.median(baseline))

    if len(ratios) < CONDITIONS_MIN_REGULARS:
        return None
    return Conditions(ratio=statistics.median(ratios), regulars=len(ratios))


def latest_event(stored_events):
    """The most recent curated race, for the dashboard's headline strip."""
    included, _ = curation.partition(stored_events)
    dated = [(parse_date(s.date_text), s) for s in included]
    dated = [(d, s) for d, s in dated if d]
    return max(dated, key=lambda pair: pair[0]) if dated else (None, None)


def standings(athletes: dict[str, Athlete], series: str, *, season: int | None = None):
    """Club standings within one series, best speed first."""
    rows = []
    for athlete in athletes.values():
        runs = [
            p
            for p in athlete.in_series(series)
            if p.speed_kmh and (season is None or season_of(p.when) == season)
        ]
        if not runs:
            continue
        best = max(runs, key=lambda p: p.speed_kmh)
        rows.append((athlete, best, len(runs)))
    rows.sort(key=lambda item: item[1].speed_kmh, reverse=True)
    return rows
