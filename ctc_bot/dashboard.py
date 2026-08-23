"""Build the club dashboard as one self-contained HTML page.

All statistics come from :mod:`ctc_bot.metrics`; this module only lays them out.

The page embeds its data as JSON and draws charts in plain SVG from vanilla
JavaScript. No CDN, no build step, no external request - it opens from a file
or from the local server either way.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from . import config, curation
from . import identity as idn
from . import metrics
from . import store

OUT_DIR = Path(__file__).resolve().parent.parent / "out"
OUT_PATH = OUT_DIR / "dashboard.html"

SERIES_LABELS = {
    "aquathon": "Aquathon",
}


def series_label(key: str, *, name_the_route: bool = True) -> str:
    """Human label for a series key.

    The route is only named when more than one route of that race type is
    actually shown. With a single enabled route there is nothing to tell apart,
    and "Time trial" reads better than "Time trial - Short (13 km)".
    """
    if key in SERIES_LABELS:
        return SERIES_LABELS[key]
    race_type, _, route = key.partition("|")
    base = "Time trial" if race_type == "time_trial" else race_type.replace("_", " ").title()
    return f"{base} — {route}" if route and name_the_route else base


# The tab order shown on the dashboard. Anything unlisted follows, alphabetically.
SERIES_ORDER = ("time_trial", "aquathon")


def _series_sort_key(key: str) -> tuple:
    race_type = key.partition("|")[0]
    rank = SERIES_ORDER.index(race_type) if race_type in SERIES_ORDER else len(SERIES_ORDER)
    return (rank, key)


def _format_time(seconds: float) -> str:
    return idn.format_time(seconds)


def build_payload(stored_events, registry: idn.Registry) -> dict:
    """Everything the page needs, in one JSON-serialisable structure."""
    included, excluded = curation.partition(stored_events)
    athletes = metrics.build(stored_events, registry)
    courses = config.load_courses()

    # Only series whose route is enabled reach the page at all. A retired route
    # is hidden rather than deleted: its events stay stored and re-enabling it
    # in data/courses.json brings the whole history back with no refetch.
    def enabled(performance) -> bool:
        return config.is_series_enabled(performance.race_type, performance.route, courses)

    def label_for(key: str) -> str:
        race_type = key.partition("|")[0]
        return series_label(key, name_the_route=len(config.enabled_routes(race_type, courses)) > 1)

    # --- athletes ---
    athlete_payload = []
    for athlete in athletes.values():
        runs = []
        for performance in sorted(
            (p for p in athlete.performances if enabled(p)), key=lambda p: p.when
        ):
            runs.append(
                {
                    "date": performance.when.isoformat(),
                    "season": metrics.season_of(performance.when),
                    "event": performance.event_code,
                    "title": performance.event_title,
                    "series": performance.series,
                    "seconds": round(performance.seconds, 1),
                    "time": _format_time(performance.seconds),
                    "speed": round(performance.speed_kmh, 2) if performance.speed_kmh else None,
                    "position": performance.position,
                    "field": performance.field_size,
                    "z": round(performance.z_score, 2) if performance.z_score is not None else None,
                    "pct": round(performance.percentile) if performance.percentile is not None else None,
                    "pb": performance.is_personal_best,
                    "raceId": performance.race_id,
                    # Only a directly claimed row can be released again; an
                    # inferred one follows the name, not a decision about it.
                    "claimed": performance.source == idn.CLAIMED,
                    "edited": performance.edited,
                    "manual": performance.manual,
                    "originalTime": (
                        _format_time(performance.original_seconds)
                        if performance.original_seconds
                        else None
                    ),
                    "additionId": performance.addition_id,
                    "legs": [round(x, 1) for x in performance.leg_seconds],
                }
            )

        if not runs:
            continue

        trends = {}
        for key in athlete.series_keys:
            if not any(r["series"] == key for r in runs):
                continue
            fitted = athlete.trend(key)
            if fitted:
                trends[key] = {"slope": round(fitted[0], 2), "r2": round(fitted[1], 2)}

        athlete_payload.append(
            {
                "id": athlete.athlete_id,
                "name": athlete.display_name,
                "verified": athlete.verified,
                "contested": athlete.contested,
                "races": len(runs),
                "first": runs[0]["date"],
                "last": runs[-1]["date"],
                "runs": runs,
                "trends": trends,
            }
        )
    athlete_payload.sort(key=lambda a: (-a["races"], a["name"].casefold()))

    # --- per-series metadata, latest race and standings ---
    series_keys = sorted({run["series"] for a in athlete_payload for run in a["runs"]})

    def latest_in(key):
        """The most recent race in one series, with each athlete's change."""
        runs = [
            (a, run) for a in athlete_payload for run in a["runs"] if run["series"] == key
        ]
        if not runs:
            return None
        newest = max(run["date"] for _, run in runs)
        rows = []
        for athlete_row, run in runs:
            if run["date"] != newest:
                continue
            earlier = [
                r["seconds"]
                for r in athlete_row["runs"]
                if r["series"] == key and r["date"] < newest
            ]
            average = sum(earlier) / len(earlier) if earlier else None
            rows.append(
                {
                    "name": athlete_row["name"],
                    "id": athlete_row["id"],
                    "position": run["position"],
                    "time": run["time"],
                    "speed": run["speed"],
                    "pb": run["pb"],
                    "vsAverage": round(run["seconds"] - average, 1) if average else None,
                }
            )
        rows.sort(key=lambda r: r["position"])
        title = next(
            (run["title"] for _, run in runs if run["date"] == newest), None
        )
        return {"date": newest, "title": title, "rows": rows}

    series_payload = []
    for key in series_keys:
        runs = [run for a in athlete_payload for run in a["runs"] if run["series"] == key]
        people = [a for a in athlete_payload if any(r["series"] == key for r in a["runs"])]
        series_payload.append(
            {
                "key": key,
                "label": label_for(key),
                "races": len({run["event"] for run in runs}),
                "results": len(runs),
                "athletes": len(people),
                "first": min(run["date"] for run in runs),
                "last": max(run["date"] for run in runs),
                "latest": latest_in(key),
            }
        )
    series_payload.sort(key=lambda s: _series_sort_key(s["key"]))

    standings_payload = {}
    for key in series_keys:
        table = metrics.standings(athletes, key)
        standings_payload[key] = [
            {
                "id": athlete.athlete_id,
                "name": athlete.display_name,
                "verified": athlete.verified,
                "best": _format_time(best.seconds),
                "speed": round(best.speed_kmh, 2) if best.speed_kmh else None,
                "races": count,
                "when": best.when.isoformat(),
            }
            for athlete, best, count in table
        ]

    # --- courses ---
    course_payload = []
    for race_type, course in courses.items():
        course_payload.append(
            {
                "type": race_type,
                "name": course.name,
                "distance": course.distance_km,
                "legs": [{"name": leg.name, "km": leg.distance_km} for leg in course.legs],
                "routes": [{"name": r.name, "km": r.distance_km} for r in course.routes],
            }
        )

    return {
        "generated": date.today().isoformat(),
        "summary": {
            "excluded": len(excluded),
            "curated": len(included),
            "athletes": len(athlete_payload),
            "results": sum(a["races"] for a in athlete_payload),
            "firstSeason": min((a["first"] for a in athlete_payload), default=""),
            "lastSeason": max((a["last"] for a in athlete_payload), default=""),
            "races": len({run["event"] for a in athlete_payload for run in a["runs"]}),
            "minRacesForTrend": idn.MIN_RACES_FOR_TREND,
            "minFieldForStats": curation.MIN_FIELD_FOR_STATS,
            "verified": sum(1 for a in athlete_payload if a["verified"]),
        },
        "seriesLabels": {key: label_for(key) for key in series_keys},
        "series": series_payload,
        "hidden": [
            {"label": series_label(f"{race_type}|{route.name}"), "distance": route.distance_km}
            for race_type, course in courses.items()
            for route in course.routes
            if not route.enabled
        ],
        "athletes": athlete_payload,
        "standings": standings_payload,
        "courses": course_payload,
        "excluded": [
            {
                "code": s.code,
                "title": s.title,
                "date": s.date_text,
                "reason": curation.assess_event(s).reason,
            }
            for s in excluded
        ],
    }


def _embed_json(payload: dict) -> str:
    """Serialise for embedding inside a ``<script>`` block.

    ``json.dumps`` does not escape ``<``, so an athlete name containing
    ``</script>`` would close the tag early and execute whatever followed.
    Athlete names come from a public entry form, so this is a real path, not a
    hypothetical one. Escaping the three HTML-significant characters as \\uXXXX
    keeps the JSON valid and inert.
    """
    return (
        json.dumps(payload, separators=(",", ":"))
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


def render(payload: dict) -> str:
    """Wrap the payload in the page."""
    return _TEMPLATE.replace("/*__DATA__*/null", _embed_json(payload))


def build(*, out_path: Path | None = None) -> Path:
    """Build the dashboard from the local store and write it to disk."""
    events = store.load_all()
    registry = idn.Registry.load()
    payload = build_payload(events, registry)

    target = out_path or OUT_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render(payload), encoding="utf-8")
    return target


_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Cork Tri Club — results</title>
<style>
  :root {
    color-scheme: light;
    --plane:#f9f9f7; --surface:#fcfcfb;
    --ink:#0b0b0b; --ink-2:#52514e; --muted:#898781;
    --grid:#e1e0d9; --axis:#c3c2b7; --border:rgba(11,11,11,0.10);
    --s1:#2a78d6; --s2:#eb6834; --s3:#1baf7a;
    --good:#006300; --bad:#d03b3b;
    --radius:10px;
  }
  @media (prefers-color-scheme: dark) {
    :root:not([data-theme="light"]) {
      color-scheme: dark;
      --plane:#0d0d0d; --surface:#1a1a19;
      --ink:#ffffff; --ink-2:#c3c2b7; --muted:#898781;
      --grid:#2c2c2a; --axis:#383835; --border:rgba(255,255,255,0.10);
      --s1:#3987e5; --s2:#d95926; --s3:#199e70;
      --good:#0ca30c; --bad:#e66767;
    }
  }
  :root[data-theme="dark"] {
    color-scheme: dark;
    --plane:#0d0d0d; --surface:#1a1a19;
    --ink:#ffffff; --ink-2:#c3c2b7; --muted:#898781;
    --grid:#2c2c2a; --axis:#383835; --border:rgba(255,255,255,0.10);
    --s1:#3987e5; --s2:#d95926; --s3:#199e70;
    --good:#0ca30c; --bad:#e66767;
  }
  * { box-sizing:border-box; }
  body {
    margin:0; background:var(--plane); color:var(--ink);
    font:15px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  }
  header {
    position:sticky; top:0; z-index:10; background:var(--surface);
    border-bottom:1px solid var(--border); padding:14px 20px;
    display:flex; gap:16px; align-items:baseline; flex-wrap:wrap;
  }
  header h1 { margin:0; font-size:17px; letter-spacing:-0.01em; }
  header .sub { color:var(--ink-2); font-size:13px; }
  header .spacer { flex:1 1 auto; }
  button, select, input[type=search], input[type=text] {
    font:inherit; color:var(--ink); background:var(--surface);
    border:1px solid var(--axis); border-radius:8px; padding:6px 10px;
  }
  button { cursor:pointer; }
  button:hover { border-color:var(--s1); }
  button.primary { background:var(--s1); color:#fff; border-color:var(--s1); }
  main { max-width:1180px; margin:0 auto; padding:20px; }
  section { background:var(--surface); border:1px solid var(--border);
            border-radius:var(--radius); padding:18px; margin-bottom:18px; }
  h2 { margin:0 0 4px; font-size:15px; letter-spacing:-0.01em; }
  .hint { color:var(--ink-2); font-size:13px; margin:0 0 14px; }
  .tiles { display:flex; gap:10px; flex-wrap:wrap; }
  .tile { flex:1 1 130px; border:1px solid var(--border); border-radius:8px; padding:10px 12px; }
  .tile .n { font-size:22px; font-weight:600; letter-spacing:-0.02em; }
  .tile .l { color:var(--ink-2); font-size:12px; }
  table { border-collapse:collapse; width:100%; font-size:13.5px; }
  th, td { text-align:left; padding:6px 10px; border-bottom:1px solid var(--grid); white-space:nowrap; }
  th { color:var(--ink-2); font-weight:600; font-size:12px; text-transform:uppercase;
       letter-spacing:0.03em; }
  td.num, th.num { text-align:right; font-variant-numeric:tabular-nums; }
  .scroll { overflow-x:auto; }
  .row { display:flex; gap:10px; flex-wrap:wrap; align-items:center; margin-bottom:12px; }
  .pill { font-size:11px; padding:2px 7px; border-radius:999px;
          border:1px solid var(--axis); color:var(--ink-2); }
  .pill.unverified { border-color:var(--s2); color:var(--s2); }
  .pill.pb { border-color:var(--good); color:var(--good); }
  .good { color:var(--good); } .bad { color:var(--bad); }
  .legend { display:flex; gap:14px; flex-wrap:wrap; font-size:12px; color:var(--ink-2);
            margin:2px 0 8px; }
  .legend i { display:inline-block; width:10px; height:10px; border-radius:3px; margin-right:5px; }
  .athlete-list { max-height:320px; overflow:auto; border:1px solid var(--border);
                  border-radius:8px; }
  .athlete-list button { display:block; width:100%; text-align:left; border:0;
                         border-bottom:1px solid var(--grid); border-radius:0; padding:7px 10px;
                         background:transparent; }
  .athlete-list button:hover { background:var(--plane); }
  .athlete-list button[aria-current="true"] { background:var(--plane); font-weight:600; }
  .grid2 { display:grid; grid-template-columns:300px 1fr; gap:18px; }
  @media (max-width:860px) { .grid2 { grid-template-columns:1fr; } }
  figure { margin:0; }
  figcaption { color:var(--ink-2); font-size:12px; margin-top:6px; }
  svg { display:block; max-width:100%; overflow:visible; }
  .tooltip { position:fixed; pointer-events:none; z-index:50; background:var(--surface);
             border:1px solid var(--axis); border-radius:8px; padding:7px 9px; font-size:12.5px;
             box-shadow:0 6px 18px rgba(0,0,0,.14); opacity:0; transition:opacity .08s; }
  .series-tabs { display:flex; gap:8px; flex-wrap:wrap; margin-bottom:18px; }
  .series-tabs button {
    padding:9px 16px; border-radius:999px; font-weight:600; font-size:14px;
    background:var(--surface); border:1px solid var(--axis);
  }
  .series-tabs button[aria-selected="true"] {
    background:var(--s1); color:#fff; border-color:var(--s1);
  }
  .series-tabs button .c { font-weight:400; opacity:.75; margin-left:6px; font-size:12.5px; }
  .tabs { display:flex; gap:6px; flex-wrap:wrap; margin-bottom:10px; }
  .year-tabs button { padding:4px 12px; font-size:13px; border-radius:7px; }
  .year-tabs button[aria-selected="true"] { background:var(--s1); color:#fff; border-color:var(--s1); }
  h3.table-head { font-size:13px; color:var(--ink-2); font-weight:600; margin:18px 0 6px;
                  text-transform:uppercase; letter-spacing:0.03em; }
  details { margin-top:10px; }
  summary { cursor:pointer; color:var(--ink-2); font-size:13px; }
  .note { border-left:3px solid var(--s2); padding:6px 10px; margin:10px 0;
          color:var(--ink-2); font-size:13px; background:var(--plane); border-radius:0 6px 6px 0; }
  .claim { border:1px solid var(--border); border-radius:8px; padding:12px; margin-top:14px; }
  .claim .msg { font-size:13px; margin-top:8px; }
  .claim .steps { margin:8px 0 10px; padding-left:20px; }
  .claim .steps li { margin:3px 0; }
  .link-btn { background:none; border:0; padding:2px 6px; color:var(--s1);
              font-size:12.5px; cursor:pointer; border-radius:6px; }
  .link-btn:hover { background:var(--plane); text-decoration:underline; }
  .link-btn.disown { color:var(--bad); }
  .link-btn:disabled { opacity:.5; cursor:default; text-decoration:none; }
  .nowrap { white-space:nowrap; }
  /* Corrected and hand-added results are never presented as if they came
     straight from the timing system. */
  tr.edited, tr.manual { background:color-mix(in srgb, var(--s2) 7%, transparent); }
  tr.manual td:first-child { box-shadow:inset 3px 0 0 var(--s2); }
  tr.edited td:first-child { box-shadow:inset 3px 0 0 var(--s2); }
  .pill.mark { border-color:var(--s2); color:var(--s2); margin-left:6px; }
  .was { font-size:11px; color:var(--muted); text-decoration:line-through; }
</style>
</head>
<body>
<div class="tooltip" id="tip" role="status" aria-live="polite"></div>

<header>
  <h1>Cork Tri Club</h1>
  <span class="sub" id="headline"></span>
  <span class="spacer"></span>
  <button id="theme" title="Switch light/dark">Theme</button>
</header>

<main>
  <nav class="series-tabs" id="series-tabs" role="tablist" aria-label="Race series"></nav>

  <section>
    <h2 id="series-title"></h2>
    <p class="hint" id="series-sub"></p>
    <div class="tiles" id="tiles"></div>
  </section>

  <section id="latest-section">
    <h2>Latest race</h2>
    <p class="hint" id="latest-sub"></p>
    <div class="scroll"><table id="latest"></table></div>
  </section>

  <section>
    <h2>Am I improving?</h2>
    <p class="hint" id="trend-hint"></p>
    <div class="grid2">
      <div>
        <div class="row">
          <input type="search" id="search" placeholder="Search athletes…" style="flex:1 1 auto">
        </div>
        <div class="athlete-list" id="list" role="listbox" aria-label="Athletes"></div>
      </div>
      <div id="athlete-panel"></div>
    </div>
  </section>

  <section>
    <h2>Club standings</h2>
    <p class="hint">Ranked by each athlete's fastest time on this course.</p>
    <div class="scroll"><table id="standings"></table></div>
  </section>

  <section>
    <h2>How this is measured</h2>
    <div id="courses"></div>
    <div id="hidden-note"></div>
    <details>
      <summary>Events excluded from these figures (<span id="exc-count"></span>)</summary>
      <div class="scroll"><table id="excluded"></table></div>
    </details>
  </section>
</main>

<script>
const DATA = /*__DATA__*/null;
const $ = (id) => document.getElementById(id);
const fmt = (n, d=1) => n === null || n === undefined ? "—" : n.toFixed(d);
const SERIES_COLORS = ["var(--s1)", "var(--s2)", "var(--s3)"];
const seriesKeys = DATA.series.map(s => s.key);
const colorOf = (key) => SERIES_COLORS[seriesKeys.indexOf(key) % SERIES_COLORS.length];
const seriesOf = (key) => DATA.series.find(s => s.key === key);
const S = DATA.summary;

const state = { series: seriesKeys[0], athlete: null, filter: "", year: "all" };

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c =>
    ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;" }[c]));
}
const year = (iso) => (iso || "").slice(0, 4);

/* ---------- theme ---------- */
$("theme").onclick = () => {
  const root = document.documentElement;
  const dark = getComputedStyle(root).getPropertyValue("--plane").trim() === "#0d0d0d";
  root.setAttribute("data-theme", dark ? "light" : "dark");
  renderSeries();
};

/* ---------- tooltip ---------- */
const tip = $("tip");
function showTip(evt, html) {
  tip.innerHTML = html;
  tip.style.opacity = 1;
  const pad = 14;
  let x = evt.clientX + pad, y = evt.clientY + pad;
  const r = tip.getBoundingClientRect();
  if (x + r.width > innerWidth) x = evt.clientX - r.width - pad;
  if (y + r.height > innerHeight) y = evt.clientY - r.height - pad;
  tip.style.left = x + "px"; tip.style.top = y + "px";
}
const hideTip = () => (tip.style.opacity = 0);

$("headline").textContent =
  `${S.athletes} athletes · ${S.results} results · ${year(S.firstSeason)}–${year(S.lastSeason)}`;

/* ---------- series tabs ---------- */
function renderTabs() {
  $("series-tabs").innerHTML = DATA.series.map(s =>
    `<button role="tab" data-k="${esc(s.key)}" aria-selected="${s.key === state.series}">
       ${esc(s.label)}<span class="c">${s.races} races</span></button>`).join("");
  [...$("series-tabs").querySelectorAll("button")].forEach(b => {
    b.onclick = () => {
      if (state.series === b.dataset.k) return;
      state.series = b.dataset.k;
      state.athlete = null;
      state.year = "all";
      renderTabs();
      renderSeries();
    };
  });
}

/* ---------- everything below the tabs ---------- */
function athletesInSeries() {
  return DATA.athletes
    .map(a => ({ ...a, seriesRuns: a.runs.filter(r => r.series === state.series) }))
    .filter(a => a.seriesRuns.length)
    .sort((x, y) => y.seriesRuns.length - x.seriesRuns.length ||
                    x.name.localeCompare(y.name));
}

function renderSeries() {
  const s = seriesOf(state.series);
  $("series-title").textContent = s.label;
  $("series-sub").textContent =
    `${year(s.first)}–${year(s.last)}. Field statistics are withheld where fewer ` +
    `than ${S.minFieldForStats} people finished.`;
  $("tiles").innerHTML = [
    [s.races, "races"],
    [s.athletes, "athletes"],
    [s.results, "results"],
    [S.verified, "identities confirmed"],
  ].map(([n, l]) => `<div class="tile"><div class="n">${n}</div><div class="l">${l}</div></div>`).join("");

  $("trend-hint").innerHTML =
    `Pick an athlete to see their ${esc(s.label)} history. A fitted direction ` +
    `needs at least ${S.minRacesForTrend} races on this course.`;

  renderLatest(s);
  renderList();
  renderStandings();

  const people = athletesInSeries();
  showAthlete(state.athlete
    ? people.find(a => a.id === state.athlete.id) || people[0]
    : people[0]);
}

function renderLatest(s) {
  const L = s.latest;
  if (!L) { $("latest-section").style.display = "none"; return; }
  $("latest-section").style.display = "";
  $("latest-sub").textContent = `${L.title || ""} — ${L.date}`;
  $("latest").innerHTML =
    `<thead><tr><th class="num">#</th><th>Athlete</th><th class="num">Time</th>
      <th class="num">km/h</th><th class="num">vs their average</th><th></th></tr></thead><tbody>` +
    L.rows.map(r => {
      let vs = "—", cls = "";
      if (r.vsAverage !== null && r.vsAverage !== undefined) {
        const faster = r.vsAverage < 0;
        cls = faster ? "good" : "bad";
        vs = (faster ? "−" : "+") + Math.abs(r.vsAverage).toFixed(1) + "s";
      }
      return `<tr><td class="num">${r.position}</td><td>${esc(r.name)}</td>
        <td class="num">${r.time}</td><td class="num">${fmt(r.speed)}</td>
        <td class="num ${cls}">${vs}</td>
        <td>${r.pb ? '<span class="pill pb">PB</span>' : ""}</td></tr>`;
    }).join("") + "</tbody>";
}

function renderList() {
  const q = state.filter.trim().toLowerCase();
  const rows = athletesInSeries().filter(a => !q || a.name.toLowerCase().includes(q));
  $("list").innerHTML = rows.slice(0, 400).map(a =>
    `<button role="option" data-id="${esc(a.id)}"
       aria-current="${state.athlete && state.athlete.id === a.id}">
       ${esc(a.name)}<span style="color:var(--muted)"> · ${a.seriesRuns.length} race${a.seriesRuns.length === 1 ? "" : "s"}</span>
     </button>`).join("") || `<div style="padding:10px;color:var(--ink-2)">No match.</div>`;
  [...$("list").querySelectorAll("button")].forEach(b => {
    b.onclick = () => showAthlete(athletesInSeries().find(a => a.id === b.dataset.id));
  });
}
$("search").oninput = (e) => { state.filter = e.target.value; renderList(); };

/* ---------- trend chart ---------- */
function lineChart(runs, key) {
  const W = 640, H = 240, m = { t: 14, r: 18, b: 30, l: 46 };
  const pts = runs.filter(r => r.speed !== null);
  if (!pts.length) return "";

  const xs = pts.map(p => new Date(p.date).getTime());
  const ys = pts.map(p => p.speed);
  const x0 = Math.min(...xs), x1 = Math.max(...xs);
  let y0 = Math.min(...ys), y1 = Math.max(...ys);
  const padY = Math.max((y1 - y0) * 0.15, 0.4);
  y0 -= padY; y1 += padY;

  const sx = v => m.l + ((v - x0) / ((x1 - x0) || 1)) * (W - m.l - m.r);
  const sy = v => H - m.b - ((v - y0) / ((y1 - y0) || 1)) * (H - m.t - m.b);

  let grid = "";
  for (let i = 0; i <= 4; i++) {
    const v = y0 + (i / 4) * (y1 - y0), y = sy(v);
    grid += `<line x1="${m.l}" x2="${W - m.r}" y1="${y}" y2="${y}" stroke="var(--grid)" stroke-width="1"/>
             <text x="${m.l - 8}" y="${y + 4}" text-anchor="end" font-size="11" fill="var(--muted)">${v.toFixed(1)}</text>`;
  }

  let xlab = "";
  [...new Set(pts.map(p => p.season))].forEach(yr => {
    const first = pts.find(p => p.season === yr);
    xlab += `<text x="${sx(new Date(first.date).getTime())}" y="${H - 8}" text-anchor="middle"
                   font-size="11" fill="var(--muted)">${yr}</text>`;
  });

  const path = pts.map((p, i) =>
    (i ? "L" : "M") + sx(new Date(p.date).getTime()).toFixed(1) + " " + sy(p.speed).toFixed(1)).join(" ");
  const color = colorOf(key);
  const dots = pts.map(p => {
    const t = `<b>${p.time}</b> · ${fmt(p.speed,1)} km/h<br>${p.date} · ${esc(p.title||"")}<br>` +
              `#${p.position} of ${p.field}${p.z!==null?` · z ${p.z>0?"+":""}${p.z}`:""}${p.pb?" · personal best":""}`;
    const cx = sx(new Date(p.date).getTime()).toFixed(1), cy = sy(p.speed).toFixed(1);
    const amended = p.manual || p.edited;
    const note = p.manual ? " · added by hand" : p.edited ? ` · corrected from ${p.originalTime}` : "";
    // Amended points are drawn hollow in the warning hue, so a chart never
    // presents a hand-entered figure as if the timer produced it.
    return `<circle cx="${cx}" cy="${cy}" r="${p.pb ? 6 : 4.5}"
      fill="${amended ? "var(--surface)" : p.pb ? "var(--good)" : color}"
      stroke="${amended ? "var(--s2)" : "var(--surface)"}" stroke-width="${amended ? 2.5 : 2}"
      data-t="${esc(t + note)}" style="cursor:pointer"/>`;
  }).join("");

  return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Speed over time">
    ${grid}<line x1="${m.l}" x2="${W - m.r}" y1="${H - m.b}" y2="${H - m.b}" stroke="var(--axis)"/>
    <path d="${path}" fill="none" stroke="${color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
    ${dots}${xlab}
    <text x="${m.l - 8}" y="${m.t + 2}" text-anchor="end" font-size="10" fill="var(--muted)">km/h</text>
  </svg>`;
}

/* Least-squares fit of speed against date, so the verdict always describes the
   points actually on screen rather than an all-time figure above a one-year
   chart. Returns km/h per year plus r². */
function fitTrend(pts) {
  const usable = pts.filter(p => p.speed !== null);
  if (usable.length < S.minRacesForTrend) return null;
  const xs = usable.map(p => new Date(p.date).getTime() / 86400000);
  const ys = usable.map(p => p.speed);
  const mx = xs.reduce((a, b) => a + b, 0) / xs.length;
  const my = ys.reduce((a, b) => a + b, 0) / ys.length;
  const sxx = xs.reduce((acc, x) => acc + (x - mx) ** 2, 0);
  if (!sxx) return null;
  const slope = xs.reduce((acc, x, i) => acc + (x - mx) * (ys[i] - my), 0) / sxx;
  const ssRes = ys.reduce((acc, y, i) => acc + (y - (slope * (xs[i] - mx) + my)) ** 2, 0);
  const ssTot = ys.reduce((acc, y) => acc + (y - my) ** 2, 0);
  return { slope: slope * 365.25, r2: ssTot ? 1 - ssRes / ssTot : 0 };
}

function showAthlete(a) {
  state.athlete = a || null;
  renderList();
  if (!a) { $("athlete-panel").innerHTML = ""; return; }

  const s = seriesOf(state.series);
  const allRuns = a.seriesRuns;
  const years = [...new Set(allRuns.map(r => r.season))].sort((x, y) => y - x);

  // Default to the most recent year this athlete raced; keep the chosen view
  // when it still applies to whoever is selected now.
  if (state.year !== "all" && !years.includes(state.year)) state.year = years[0];

  const runs = state.year === "all" ? allRuns : allRuns.filter(r => r.season === state.year);
  const t = fitTrend(runs);

  let verdict;
  if (!t) {
    const scope = state.year === "all" ? "in total" : `in ${state.year}`;
    verdict = `${runs.length} race${runs.length === 1 ? "" : "s"} ${scope} — a direction needs at least ${S.minRacesForTrend}.`;
  } else {
    const slope = t.slope.toFixed(2), r2 = t.r2.toFixed(2);
    const dir = t.slope > 0.05 ? "faster" : t.slope < -0.05 ? "slower" : "about level";
    const cls = t.slope > 0.05 ? "good" : t.slope < -0.05 ? "bad" : "";
    verdict = `Trending <span class="${cls}">${dir}</span>: ${t.slope > 0 ? "+" : ""}${slope} km/h per year (fit r² ${r2}).`;
  }

  let html = `<div class="row"><h2 style="margin:0">${esc(a.name)}</h2>
    ${a.verified ? '<span class="pill">identity confirmed</span>'
      : a.contested ? '<span class="pill unverified">contested — two people share this name</span>'
      : '<span class="pill unverified">unverified</span>'}
    <span class="pill">${runs.length} ${esc(s.label)} race${runs.length === 1 ? "" : "s"}</span>
    <span class="pill">${year(runs[0].date)}–${year(runs[runs.length-1].date)}</span></div>`;

  if (a.contested) {
    html += `<div class="note"><b>These results are probably not all one person.</b>
      This name appears twice in the same race, which means either two people race
      under it or somebody was entered twice. Nothing has been hidden or guessed —
      the results are all shown as found. Confirming below is how they get split
      apart.</div>`;
  } else if (!a.verified) {
    html += `<div class="note"><b>Nobody has confirmed who this is yet.</b>
      These results are grouped only because the same name was typed on the entry
      sheet. Any other spelling that rider used is sitting elsewhere in the list as
      a separate entry, and if two people share the name their results are mixed
      together here.</div>`;
  }

  const yearTabs = years.map(y =>
    `<button class="year" data-y="${y}" aria-selected="${state.year === y}">${y}</button>`
  ).join("") +
    `<button class="year" data-y="all" aria-selected="${state.year === "all"}">All time</button>`;

  html += `<figure style="margin-top:16px">
    <div class="tabs year-tabs">${yearTabs}</div>
    <div class="legend"><span><i style="background:${colorOf(state.series)}"></i>${esc(s.label)}</span>
      <span><i style="background:var(--good)"></i>personal best</span>
      ${allRuns.some(r => r.manual || r.edited)
        ? '<span><i style="background:var(--surface);box-shadow:inset 0 0 0 2px var(--s2)"></i>corrected or added by hand</span>'
        : ""}</div>
    ${lineChart(runs, state.series)}
    <figcaption>${verdict}</figcaption>
  </figure>
  <h3 class="table-head">All ${esc(s.label)} results, most recent first</h3>
  <div class="scroll"><table>
    <thead><tr><th>Date</th><th class="num">Time</th><th class="num">km/h</th>
      ${a.verified ? "<th></th>" : ""}</tr></thead>
    <tbody>${allRuns.slice().reverse().map(r => `<tr class="${r.manual ? "manual" : r.edited ? "edited" : ""}">
      <td>${r.date}
        ${r.manual ? '<span class="pill mark" title="Never recorded by the timing system">added by hand</span>' : ""}
        ${r.edited ? `<span class="pill mark" title="Published time was ${esc(r.originalTime || "")}">corrected</span>` : ""}
      </td>
      <td class="num">${r.time}${r.pb ? ' <span class="pill pb">PB</span>' : ""}
        ${r.edited ? `<div class="was">was ${esc(r.originalTime || "")}</div>` : ""}
      </td>
      <td class="num">${fmt(r.speed)}</td>
      ${a.verified ? `<td class="num nowrap">${rowActions(r)}</td>` : ""}
    </tr>`).join("")}</tbody></table></div>` + claimForm(a) + adoptPanel(a) + addRacePanel(a);

  $("athlete-panel").innerHTML = html;
  $("athlete-panel").querySelectorAll("button.year").forEach(b => {
    b.onclick = () => {
      state.year = b.dataset.y === "all" ? "all" : Number(b.dataset.y);
      showAthlete(a);
    };
  });
  const panel = $("athlete-panel");
  panel.querySelectorAll("button.disown:not(.remove-added)").forEach(b => {
    b.onclick = () => postRow("/api/disown", a, b.dataset.e, b.dataset.r, b);
  });
  panel.querySelectorAll("button.reset-time").forEach(b => {
    b.onclick = () => postRow("/api/reset-time", a, b.dataset.e, b.dataset.r, b);
  });
  panel.querySelectorAll("button.edit-time").forEach(b => {
    b.onclick = () => {
      const entered = prompt(
        "Corrected time for this race (e.g. 25:37.2).\n\n" +
        "The published time is kept, so this can be reset at any point.",
        b.dataset.t);
      if (entered === null || !entered.trim()) return;
      postRow("/api/edit-time", a, b.dataset.e, b.dataset.r, b, { time: entered.trim() });
    };
  });
  panel.querySelectorAll("button.remove-added").forEach(b => {
    b.onclick = () => postJson("/api/remove-result", { additionId: b.dataset.a }, b, $("row-msg"));
  });
  wireAdopt(a);
  wireAddRace(a);
  $("athlete-panel").querySelectorAll("circle[data-t]").forEach(c => {
    c.addEventListener("mousemove", e => showTip(e, c.dataset.t));
    c.addEventListener("mouseleave", hideTip);
  });
  wireClaim(a);
}

/* ---------- claim ---------- */
function claimForm(a) {
  return `<div class="claim">
    <h2>Is this you?</h2>
    <p class="hint">
      Results are matched only by the name written on the entry sheet, and that
      name changes from week to week — <b>James Carron</b>, <b>James carron</b>,
      <b>James Carrons</b> and <b>James C</b> were all the same rider. Until
      somebody says so, each spelling looks like a different person and each one
      gets its own short, broken history.
    </p>
    <ol class="hint steps">
      <li>Put your full name in the box — the version you want shown.</li>
      <li>Confirm the results on this page are yours.</li>
      <li>Repeat for any <i>other</i> spelling you find in the list on the left.</li>
    </ol>
    <p class="hint">
      Every spelling you confirm gets joined to the same person, across both the
      time trial and the aquathon, and future races under any of those spellings
      are recognised on their own. Nothing is deleted and nothing is guessed —
      only confirm results you actually raced.
    </p>
    <div class="row">
      <input type="text" id="claim-name" placeholder="Your full name" value="${esc(a.name)}" style="flex:1 1 220px">
      <button class="primary" id="claim-go">These ${a.races} results are mine</button>
    </div>
    <div class="msg" id="claim-msg"></div>
  </div>`;
}

/* Per-row controls. A hand-added result can only be removed; a real one can be
   corrected, reset to its published time, or released back to its entry name. */
function rowActions(r) {
  if (r.manual) {
    return `<button class="link-btn disown remove-added" data-a="${esc(r.additionId)}"
              title="Delete this hand-added result">Remove</button>`;
  }
  const edit = `<button class="link-btn edit-time" data-e="${esc(r.event)}"
      data-r="${esc(r.raceId)}" data-t="${esc(r.time)}"
      title="Correct this time">Edit</button>`;
  const reset = r.edited
    ? `<button class="link-btn reset-time" data-e="${esc(r.event)}" data-r="${esc(r.raceId)}"
         title="Restore the published time">Reset</button>`
    : "";
  const disown = r.claimed
    ? `<button class="link-btn disown" data-e="${esc(r.event)}" data-r="${esc(r.raceId)}"
         title="Return this result to the name on the entry list">Not mine</button>`
    : `<span class="pill" title="Matched by name, not individually confirmed">by name</span>`;
  return edit + reset + disown;
}

/* A race the timing system never captured. */
function addRacePanel(a) {
  if (!a.verified) return "";
  const types = DATA.series.map(s => {
    const raceType = s.key.split("|")[0];
    return `<option value="${esc(raceType)}">${esc(s.label)}</option>`;
  }).join("");
  return `<div class="claim">
    <h2>Add a race that was never recorded</h2>
    <p class="hint">
      For a race you rode where the timer missed you entirely, so there is no
      result to attach. It counts towards your own history and trend, is marked
      <b>added by hand</b> everywhere it appears, and can be removed again. It
      carries no finishing position, because there is no field it was measured
      against.
    </p>
    <div class="row">
      <select id="add-type">${types}</select>
      <input type="date" id="add-date">
      <input type="text" id="add-time" placeholder="Time, e.g. 25:37.2" style="width:11em">
      <input type="text" id="add-title" placeholder="Race name (optional)" style="flex:1 1 160px">
      <button class="primary" id="add-go">Add this race</button>
    </div>
    <div class="msg" id="add-msg"></div>
  </div>`;
}

/* Adding or releasing a single result. Only offered once an athlete is
   confirmed: both actions edit that person's claims, and an unconfirmed group
   has none to edit. */
function adoptPanel(a) {
  if (!a.verified) return "";
  return `<div class="claim">
    <h2>Add a result that is missing</h2>
    <p class="hint">
      Search by first name for a result recorded under a different spelling —
      an initial, a nickname, a typo — and attach it. Use <b>Not mine</b> in the
      table above to release one that is not yours; it goes straight back to the
      name printed on the entry list.
    </p>
    <div class="row">
      <input type="search" id="adopt-q" placeholder="First name, e.g. James" style="flex:1 1 220px">
      <button id="adopt-find">Find results</button>
    </div>
    <div id="adopt-results"></div>
    <div class="msg" id="row-msg"></div>
  </div>`;
}

async function postJson(url, body, button, msg) {
  msg = msg || $("row-msg") || $("claim-msg");
  if (button) button.disabled = true;
  if (msg) msg.textContent = "Saving…";
  try {
    const res = await fetch(url, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const out = await res.json();
    if (msg) {
      msg.innerHTML = out.ok
        ? `<span class="good">${esc(out.message)}</span> Refresh to see it applied.`
        : `<span class="bad">${esc(out.message || "Could not save.")}</span>`;
    }
    return out.ok;
  } catch (e) {
    if (msg) msg.innerHTML = `<span class="bad">No server — open this page via <code>ctc dashboard</code>.</span>`;
    return false;
  } finally {
    if (button) button.disabled = false;
  }
}

const postRow = (url, a, event, raceId, button, extra) =>
  postJson(url, { athleteId: a.id, event, raceId, ...(extra || {}) }, button);

function wireAddRace(a) {
  const go = $("add-go");
  if (!go) return;
  go.onclick = () => postJson("/api/add-result", {
    athleteId: a.id,
    raceType: $("add-type").value,
    date: $("add-date").value,
    seconds: $("add-time").value,
    title: $("add-title").value,
  }, go, $("add-msg"));
}

function wireAdopt(a) {
  const find = $("adopt-find");
  if (!find) return;
  const run = async () => {
    const q = $("adopt-q").value.trim();
    const box = $("adopt-results");
    if (q.length < 2) { box.innerHTML = `<p class="hint">Type at least two letters.</p>`; return; }
    box.innerHTML = `<p class="hint">Searching…</p>`;
    try {
      const res = await fetch("/api/rows?q=" + encodeURIComponent(q));
      const out = await res.json();
      const rows = (out.rows || []).filter(r => r.ownerId !== a.id);
      box.innerHTML = rows.length
        ? `<div class="scroll"><table><thead><tr><th>Date</th><th>Name</th>
             <th class="num">Time</th><th>Currently</th><th></th></tr></thead><tbody>` +
          rows.map(r => `<tr>
            <td>${esc((r.date || "").replace(/,.*$/, ""))}</td>
            <td>${esc(r.name)}</td>
            <td class="num">${esc(r.time)}</td>
            <td>${r.owner ? esc(r.owner) : '<span style="color:var(--muted)">unclaimed</span>'}</td>
            <td class="num"><button class="link-btn adopt"
                 data-e="${esc(r.event)}" data-r="${esc(r.raceId)}">Add</button></td>
          </tr>`).join("") + "</tbody></table></div>"
        : `<p class="hint">Nothing else found for that name.</p>`;
      box.querySelectorAll("button.adopt").forEach(b => {
        b.onclick = () => postRow("/api/adopt", a, b.dataset.e, b.dataset.r, b);
      });
    } catch (e) {
      box.innerHTML = `<p class="hint">No server — open this page via <code>ctc dashboard</code>.</p>`;
    }
  };
  find.onclick = run;
  $("adopt-q").onkeydown = (e) => { if (e.key === "Enter") run(); };
}

function wireClaim(a) {
  const btn = $("claim-go");
  if (!btn) return;
  btn.onclick = async () => {
    const name = $("claim-name").value.trim();
    const msg = $("claim-msg");
    if (!name) { msg.textContent = "Enter the name to record these under."; return; }
    btn.disabled = true; msg.textContent = "Saving…";
    try {
      const res = await fetch("/api/claim", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, athleteId: a.id }),
      });
      const out = await res.json();
      msg.innerHTML = out.ok
        ? `<span class="good">Saved — ${esc(out.message)}</span> Refresh to see it applied.`
        : `<span class="bad">${esc(out.message || "Could not save.")}</span>`;
    } catch (e) {
      msg.innerHTML = `<span class="bad">No server. Open this page via <code>ctc dashboard</code> to claim results.</span>`;
    } finally { btn.disabled = false; }
  };
}

/* ---------- standings ---------- */
function renderStandings() {
  const rows = (DATA.standings[state.series] || []).slice(0, 120);
  $("standings").innerHTML =
    `<thead><tr><th class="num">#</th><th>Athlete</th><th class="num">Best time</th>
      <th class="num">km/h</th><th class="num">Races</th><th>When</th></tr></thead><tbody>` +
    rows.map((r, i) => `<tr>
      <td class="num">${i + 1}</td>
      <td>${esc(r.name)} ${r.verified ? "" : '<span class="pill unverified">unverified</span>'}</td>
      <td class="num">${r.best}</td><td class="num">${fmt(r.speed, 2)}</td>
      <td class="num">${r.races}</td><td>${r.when}</td></tr>`).join("") + "</tbody>";
}

/* ---------- footer sections ---------- */
$("courses").innerHTML = DATA.courses.map(c => `
  <p class="hint" style="margin:0 0 8px"><b>${esc(c.name)}</b> —
    ${c.legs.map(l => `${esc(l.name)} ${l.km} km`).join(" + ")}
    ${c.routes.length ? "· routes: " + c.routes.map(r => `${esc(r.name)} (${r.km} km)`).join(", ") : ""}</p>`).join("") +
  `<p class="hint">Distances are configured, not taken from each event page: the pages
   disagree with themselves. Results implying an impossible speed are dropped — hand
   timing misfires both ways.</p>`;

$("hidden-note").innerHTML = DATA.hidden.length
  ? `<div class="note">Hidden for now: ${DATA.hidden.map(h => esc(h.label)).join(", ")}.
     Those events are still stored — re-enable with
     <code>ctc courses -- --enable "time_trial:Long (13.8 km)"</code>.</div>`
  : "";

$("exc-count").textContent = DATA.excluded.length;
$("excluded").innerHTML =
  `<thead><tr><th>Date</th><th>Event</th><th>Why excluded</th></tr></thead><tbody>` +
  DATA.excluded.map(e => `<tr><td>${esc(e.date || "")}</td><td>${esc(e.title || "")}</td>
    <td>${esc(e.reason)}</td></tr>`).join("") + "</tbody>";

/* ---------- go ---------- */
renderTabs();
renderSeries();
</script>
</body>
</html>
"""
