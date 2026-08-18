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


def series_label(key: str) -> str:
    if key in SERIES_LABELS:
        return SERIES_LABELS[key]
    race_type, _, route = key.partition("|")
    base = "Time trial" if race_type == "time_trial" else race_type.replace("_", " ").title()
    return f"{base} — {route}" if route else base


def _format_time(seconds: float) -> str:
    return idn.format_time(seconds)


def build_payload(stored_events, registry: idn.Registry) -> dict:
    """Everything the page needs, in one JSON-serialisable structure."""
    included, excluded = curation.partition(stored_events)
    athletes = metrics.build(stored_events, registry)
    courses = config.load_courses()

    latest_date, latest_stored = metrics.latest_event(stored_events)

    # --- athletes ---
    athlete_payload = []
    for athlete in athletes.values():
        runs = []
        for performance in sorted(athlete.performances, key=lambda p: p.when):
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
                    "legs": [round(x, 1) for x in performance.leg_seconds],
                }
            )

        trends = {}
        for key in athlete.series_keys:
            fitted = athlete.trend(key)
            if fitted:
                trends[key] = {"slope": round(fitted[0], 2), "r2": round(fitted[1], 2)}

        athlete_payload.append(
            {
                "id": athlete.athlete_id,
                "name": athlete.display_name,
                "verified": athlete.verified,
                "races": athlete.race_count,
                "first": athlete.first_raced.isoformat(),
                "last": athlete.last_raced.isoformat(),
                "runs": runs,
                "trends": trends,
            }
        )
    athlete_payload.sort(key=lambda a: (-a["races"], a["name"].casefold()))

    # --- latest race ---
    latest = None
    if latest_stored is not None:
        rows = []
        for athlete in athletes.values():
            for performance in athlete.performances:
                if performance.event_code == latest_stored.code:
                    same_series = [
                        p for p in athlete.in_series(performance.series) if p.when < performance.when
                    ]
                    previous = (
                        sum(p.seconds for p in same_series) / len(same_series)
                        if same_series
                        else None
                    )
                    rows.append(
                        {
                            "name": athlete.display_name,
                            "id": athlete.athlete_id,
                            "position": performance.position,
                            "time": _format_time(performance.seconds),
                            "speed": round(performance.speed_kmh, 1) if performance.speed_kmh else None,
                            "pb": performance.is_personal_best,
                            "vsAverage": round(performance.seconds - previous, 1) if previous else None,
                        }
                    )
        rows.sort(key=lambda r: r["position"])
        latest = {
            "code": latest_stored.code,
            "title": latest_stored.title,
            "date": latest_date.isoformat(),
            "dateText": latest_stored.date_text,
            "series": series_label(
                f"{latest_stored.race_type}|"
                + (
                    config.route_for_event(
                        latest_stored.race_type,
                        latest_stored.listing.get("listed_distance_km"),
                        courses,
                    ).name
                    if config.route_for_event(
                        latest_stored.race_type,
                        latest_stored.listing.get("listed_distance_km"),
                        courses,
                    )
                    else ""
                )
                if latest_stored.race_type != "aquathon"
                else "aquathon"
            ),
            "rows": rows,
        }

    # --- standings, per series ---
    series_keys = sorted({p.series for a in athletes.values() for p in a.performances})
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
            "races": len(included),
            "excluded": len(excluded),
            "athletes": len(athlete_payload),
            "results": sum(a["races"] for a in athlete_payload),
            "firstSeason": min((a["first"] for a in athlete_payload), default=""),
            "lastSeason": max((a["last"] for a in athlete_payload), default=""),
            "minRacesForTrend": idn.MIN_RACES_FOR_TREND,
            "minFieldForStats": curation.MIN_FIELD_FOR_STATS,
            "verified": sum(1 for a in athlete_payload if a["verified"]),
        },
        "seriesLabels": {key: series_label(key) for key in series_keys},
        "athletes": athlete_payload,
        "latest": latest,
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
  .tabs { display:flex; gap:6px; flex-wrap:wrap; margin-bottom:12px; }
  .tabs button[aria-selected="true"] { background:var(--s1); color:#fff; border-color:var(--s1); }
  details { margin-top:10px; }
  summary { cursor:pointer; color:var(--ink-2); font-size:13px; }
  .note { border-left:3px solid var(--s2); padding:6px 10px; margin:10px 0;
          color:var(--ink-2); font-size:13px; background:var(--plane); border-radius:0 6px 6px 0; }
  .claim { border:1px solid var(--border); border-radius:8px; padding:12px; margin-top:14px; }
  .claim .msg { font-size:13px; margin-top:8px; }
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
  <section>
    <h2>The club, at a glance</h2>
    <p class="hint" id="range"></p>
    <div class="tiles" id="tiles"></div>
  </section>

  <section id="latest-section">
    <h2>Latest race</h2>
    <p class="hint" id="latest-sub"></p>
    <div class="scroll"><table id="latest"></table></div>
  </section>

  <section>
    <h2>Am I improving?</h2>
    <p class="hint">
      Pick an athlete. Each course is trended separately, because times on
      different routes are not comparable. A fitted direction needs at least
      <span id="min-races"></span> races on that course.
    </p>
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
    <p class="hint">Ranked by each athlete's fastest time on that course.</p>
    <div class="tabs" id="series-tabs" role="tablist"></div>
    <div class="scroll"><table id="standings"></table></div>
  </section>

  <section>
    <h2>How this is measured</h2>
    <div id="courses"></div>
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
const seriesKeys = Object.keys(DATA.seriesLabels);
const colorOf = (key) => SERIES_COLORS[seriesKeys.indexOf(key) % SERIES_COLORS.length];
const label = (key) => DATA.seriesLabels[key] || key;

/* ---------- theme ---------- */
$("theme").onclick = () => {
  const root = document.documentElement;
  const dark = getComputedStyle(root).getPropertyValue("--plane").trim() === "#0d0d0d";
  root.setAttribute("data-theme", dark ? "light" : "dark");
  if (state.athlete) showAthlete(state.athlete);
  drawStandings();
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

/* ---------- summary ---------- */
const S = DATA.summary;
$("headline").textContent =
  `${S.races} races · ${S.athletes} athletes · ${S.results} results`;
$("range").textContent =
  `${S.firstSeason.slice(0,4)}–${S.lastSeason.slice(0,4)}. ` +
  `Field statistics are withheld where fewer than ${S.minFieldForStats} people finished.`;
$("min-races").textContent = S.minRacesForTrend;
$("tiles").innerHTML = [
  [S.races, "races counted"],
  [S.athletes, "athletes"],
  [S.results, "results"],
  [S.verified, "identities confirmed"],
  [S.excluded, "events excluded"],
].map(([n, l]) => `<div class="tile"><div class="n">${n}</div><div class="l">${l}</div></div>`).join("");

/* ---------- latest race ---------- */
if (DATA.latest) {
  const L = DATA.latest;
  $("latest-sub").textContent = `${L.title} — ${L.dateText}`;
  const head = `<thead><tr><th class="num">#</th><th>Athlete</th><th class="num">Time</th>
    <th class="num">km/h</th><th class="num">vs their average</th><th></th></tr></thead>`;
  const body = L.rows.map(r => {
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
  }).join("");
  $("latest").innerHTML = head + "<tbody>" + body + "</tbody>";
} else {
  $("latest-section").remove();
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c =>
    ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;" }[c]));
}

/* ---------- athlete list ---------- */
const state = { athlete: null, series: seriesKeys[0], filter: "" };

function renderList() {
  const q = state.filter.trim().toLowerCase();
  const rows = DATA.athletes
    .filter(a => !q || a.name.toLowerCase().includes(q))
    .slice(0, 400);
  $("list").innerHTML = rows.map(a =>
    `<button role="option" data-id="${esc(a.id)}"
       aria-current="${state.athlete && state.athlete.id === a.id}">
       ${esc(a.name)}
       <span class="l" style="color:var(--muted)"> · ${a.races} race${a.races === 1 ? "" : "s"}</span>
       ${a.verified ? "" : ''}
     </button>`).join("") || `<div style="padding:10px;color:var(--ink-2)">No match.</div>`;
  [...$("list").querySelectorAll("button")].forEach(b => {
    b.onclick = () => showAthlete(DATA.athletes.find(a => a.id === b.dataset.id));
  });
}
$("search").oninput = (e) => { state.filter = e.target.value; renderList(); };

/* ---------- trend chart ---------- */
function lineChart(runs, key) {
  const W = 640, H = 240, m = { t: 14, r: 18, b: 30, l: 46 };
  const pts = runs.filter(r => r.speed !== null);
  if (pts.length === 0) return "";

  const xs = pts.map(p => new Date(p.date).getTime());
  const ys = pts.map(p => p.speed);
  const x0 = Math.min(...xs), x1 = Math.max(...xs);
  let y0 = Math.min(...ys), y1 = Math.max(...ys);
  const padY = Math.max((y1 - y0) * 0.15, 0.4);
  y0 -= padY; y1 += padY;

  const sx = v => m.l + ((v - x0) / ((x1 - x0) || 1)) * (W - m.l - m.r);
  const sy = v => H - m.b - ((v - y0) / ((y1 - y0) || 1)) * (H - m.t - m.b);

  const ticks = 4;
  let grid = "";
  for (let i = 0; i <= ticks; i++) {
    const v = y0 + (i / ticks) * (y1 - y0), y = sy(v);
    grid += `<line x1="${m.l}" x2="${W - m.r}" y1="${y}" y2="${y}" stroke="var(--grid)" stroke-width="1"/>
             <text x="${m.l - 8}" y="${y + 4}" text-anchor="end" font-size="11"
                   fill="var(--muted)">${v.toFixed(1)}</text>`;
  }

  const years = [...new Set(pts.map(p => p.season))];
  let xlab = "";
  years.forEach(yr => {
    const first = pts.find(p => p.season === yr);
    const x = sx(new Date(first.date).getTime());
    xlab += `<text x="${x}" y="${H - 8}" text-anchor="middle" font-size="11"
                   fill="var(--muted)">${yr}</text>`;
  });

  const path = pts.map((p, i) =>
    (i ? "L" : "M") + sx(new Date(p.date).getTime()).toFixed(1) + " " + sy(p.speed).toFixed(1)
  ).join(" ");

  const color = colorOf(key);
  const dots = pts.map(p => {
    const cx = sx(new Date(p.date).getTime()), cy = sy(p.speed);
    const r = p.pb ? 6 : 4.5;
    return `<circle cx="${cx.toFixed(1)}" cy="${cy.toFixed(1)}" r="${r}"
      fill="${p.pb ? "var(--good)" : color}" stroke="var(--surface)" stroke-width="2"
      data-t="${esc(`<b>${p.time}</b> · ${fmt(p.speed,1)} km/h<br>${p.date} · ${esc(p.title||"")}<br>#${p.position} of ${p.field}${p.z!==null?` · z ${p.z>0?"+":""}${p.z}`:""}${p.pb?" · personal best":""}`)}"
      style="cursor:pointer"/>`;
  }).join("");

  return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Speed over time">
    ${grid}
    <line x1="${m.l}" x2="${W - m.r}" y1="${H - m.b}" y2="${H - m.b}" stroke="var(--axis)"/>
    <path d="${path}" fill="none" stroke="${color}" stroke-width="2"
          stroke-linejoin="round" stroke-linecap="round"/>
    ${dots}${xlab}
    <text x="${m.l - 8}" y="${m.t + 2}" text-anchor="end" font-size="10" fill="var(--muted)">km/h</text>
  </svg>`;
}

function bindDots(root) {
  root.querySelectorAll("circle[data-t]").forEach(c => {
    c.addEventListener("mousemove", e => showTip(e, c.dataset.t));
    c.addEventListener("mouseleave", hideTip);
  });
}

function showAthlete(a) {
  if (!a) return;
  state.athlete = a;
  renderList();

  const bySeries = {};
  a.runs.forEach(r => (bySeries[r.series] ||= []).push(r));

  let html = `<div class="row"><h2 style="margin:0">${esc(a.name)}</h2>
    ${a.verified
      ? '<span class="pill">identity confirmed</span>'
      : '<span class="pill unverified">unverified — may combine more than one person</span>'}
    <span class="pill">${a.races} race${a.races === 1 ? "" : "s"}</span>
    <span class="pill">${a.first.slice(0,4)}–${a.last.slice(0,4)}</span></div>`;

  if (!a.verified) {
    html += `<div class="note">Grouped only by the exact name typed on the entry list.
      Over seven years that may be more than one person. Claim these results below to confirm.</div>`;
  }

  Object.keys(bySeries).sort().forEach(key => {
    const runs = bySeries[key];
    const t = a.trends[key];
    let verdict;
    if (!t) {
      verdict = `Needs ${S.minRacesForTrend} races on this course to fit a direction (has ${runs.length}).`;
    } else {
      const dir = t.slope > 0.05 ? "faster" : t.slope < -0.05 ? "slower" : "about level";
      const cls = t.slope > 0.05 ? "good" : t.slope < -0.05 ? "bad" : "";
      verdict = `Trending <span class="${cls}">${dir}</span>: ` +
        `${t.slope > 0 ? "+" : ""}${t.slope} km/h per year (fit r² ${t.r2}).`;
    }
    html += `<figure style="margin-top:16px">
      <div class="legend"><span><i style="background:${colorOf(key)}"></i>${esc(label(key))}</span>
        <span><i style="background:var(--good)"></i>personal best</span></div>
      ${lineChart(runs, key)}
      <figcaption>${verdict}</figcaption>
    </figure>
    <div class="scroll"><table>
      <thead><tr><th>Date</th><th>Race</th><th class="num">Time</th><th class="num">km/h</th>
        <th class="num">Place</th><th class="num">vs field</th></tr></thead>
      <tbody>${runs.map(r => `<tr>
        <td>${r.date}</td><td>${esc(r.title || "")}</td>
        <td class="num">${r.time}${r.pb ? ' <span class="pill pb">PB</span>' : ""}</td>
        <td class="num">${fmt(r.speed)}</td>
        <td class="num">${r.position} / ${r.field}</td>
        <td class="num">${r.z === null ? "—" : (r.z > 0 ? "+" : "") + r.z}</td>
      </tr>`).join("")}</tbody></table></div>`;
  });

  html += claimForm(a);
  $("athlete-panel").innerHTML = html;
  bindDots($("athlete-panel"));
  wireClaim(a);
}

/* ---------- claim form ---------- */
function claimForm(a) {
  return `<div class="claim">
    <h2>Are these your results?</h2>
    <p class="hint">Confirming links every spelling you have raced under, so future
      races are recognised automatically.</p>
    <div class="row">
      <input type="text" id="claim-name" placeholder="Your full name" value="${esc(a.name)}"
             style="flex:1 1 220px">
      <button class="primary" id="claim-go">Confirm these ${a.races} result${a.races===1?"":"s"} are mine</button>
    </div>
    <div class="msg" id="claim-msg"></div>
  </div>`;
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
        body: JSON.stringify({
          name,
          rows: a.runs.map(r => ({ event: r.event, athleteId: a.id })),
          athleteId: a.id,
        }),
      });
      const out = await res.json();
      msg.innerHTML = out.ok
        ? `<span class="good">Saved — ${esc(out.message)}</span> Rebuild the page to see it applied.`
        : `<span class="bad">${esc(out.message || "Could not save.")}</span>`;
    } catch (e) {
      msg.innerHTML = `<span class="bad">No server. Open this page via <code>ctc dashboard</code>
        to claim results.</span>`;
    } finally { btn.disabled = false; }
  };
}

/* ---------- standings ---------- */
function drawStandings() {
  $("series-tabs").innerHTML = seriesKeys.map(k =>
    `<button role="tab" data-k="${esc(k)}" aria-selected="${k === state.series}">
      ${esc(label(k))}</button>`).join("");
  [...$("series-tabs").querySelectorAll("button")].forEach(b => {
    b.onclick = () => { state.series = b.dataset.k; drawStandings(); };
  });

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

/* ---------- courses & exclusions ---------- */
$("courses").innerHTML = DATA.courses.map(c => `
  <p class="hint" style="margin:0 0 8px">
    <b>${esc(c.name)}</b> — ${c.legs.map(l => `${esc(l.name)} ${l.km} km`).join(" + ")}
    ${c.routes.length
      ? "· routes: " + c.routes.map(r => `${esc(r.name)} (${r.km} km)`).join(", ")
      : ""}
  </p>`).join("") +
  `<p class="hint">Distances are configured, not taken from each event page: the pages
   disagree with themselves. Speed always uses the configured course.</p>`;

$("exc-count").textContent = DATA.excluded.length;
$("excluded").innerHTML =
  `<thead><tr><th>Date</th><th>Event</th><th>Why excluded</th></tr></thead><tbody>` +
  DATA.excluded.map(e => `<tr><td>${esc(e.date || "")}</td><td>${esc(e.title || "")}</td>
    <td>${esc(e.reason)}</td></tr>`).join("") + "</tbody>";

/* ---------- go ---------- */
renderList();
drawStandings();
if (DATA.athletes.length) showAthlete(DATA.athletes[0]);
</script>
</body>
</html>
"""
