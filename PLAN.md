# CTC_bot — Plan

A bot that harvests RaceClocker links from the club's triathlon WhatsApp chat,
parses each event's results, and renders per-athlete trend lines.

Status: **Phase 0 complete** — the parsing assumption is verified end to end
against a live event, and the TT/aquathon classifier is built and tested.

---

## 1. What has been verified (not assumed)

I pulled `https://raceclocker.com/7eecd645` and inspected the source before
designing anything. Findings that shape the whole build:

| Finding | Consequence |
|---|---|
| Results are **server-rendered** into a `let AllResults = [...]` JSON array in the HTML | No headless browser, no JS execution, no DOM scraping. A `requests` GET plus `json.loads` is the whole scraper. This is the single biggest de-risking factor. |
| Event metadata sits in sibling scalars: `Distance = "13"`, `DistanceUnit = "km"`, `SplitNames`, `AllCategories`, `<span id="pagetitle">` | Full event context available from one request. |
| The header prints the weekday **literally** — `Tuesday 18 Aug '26, 19:00` | Tue/Thu series classification needs no calendar arithmetic. |
| Timing slots run `TmSplit1..TmSplit6`; unused slots are `"00:00:00"` | Counting populated slots gives the structural fingerprint: 2 points = 1 leg = TT; 3 points = 2 legs = aquathon. |
| **`Rank` is not trustworthy** — in this event it mirrors bib order (Gedis is "Rank 1" at 21:14, but Kevin G ran 20:12) | The pipeline must recompute rank from `TmResultSec`. Pinned as a regression test. |
| `TmResultSec` is the authoritative numeric result (e.g. `1274.0`) | Use it, not the formatted `Result` string, for all maths. |
| Split times need their companion `...dc` deci-second field | Ignoring `dc` puts every result out by up to 0.9s. With it, computed elapsed reconciles with `TmResultSec` for **24/24 athletes across both sample events**. |
| **`SplitNames` are a generic triathlon template, not ground truth** | In the aquathon the real finish sits in the slot labelled `"Run start"`, while slot 6 (`"Finish"`) is empty. Legs are derived from *populated slots*, never from labels. |
| TT uses **individual starts** (15 distinct start times); the aquathon is a **mass start** (1 shared start time) | A fourth structural signal, and it confirms elapsed time must always be computed per athlete rather than from a single gun time. |
| No per-leg distances are published (`AllCustomLapNamesAndDistances`, `CategoryDistanceOverrides`, `WaveDistanceOverrides` are all empty); the aquathon's `Distance` is a single combined `8.0 km` | Swim/run leg *pace* cannot be derived from the page. Leg distances must come from config. |
| The page also loads Ably websockets for live updates | Live in-race tracking is possible later, but is explicitly out of scope for v1. |

Both sample events are archived as test fixtures, so any future parser change is
checked against real pages rather than assumptions.

## 2. Pipeline

```
  discovery                parse              normalise            analyse           render
+--------------+   +------------------+   +--------------+   +--------------+   +--------------+
| WhatsApp     |   | raceclocker.py   |   | identity.py  |   | metrics.py   |   | HTML board   |
|  export .txt |-->|  fetch + parse   |-->| alias map    |-->| per-athlete  |-->| + PNG export |
| linked device|   | classify.py      |   | canonical id |   | series       |   |              |
| admin list   |   |  TT vs aquathon  |   |              |   |              |   |              |
+--------------+   +------------------+   +--------------+   +--------------+   +--------------+
       |                    |
       |            data/raw/<code>.html      <- archived verbatim, so any parse
       |            data/events/<code>.json      change can be replayed offline
       +-- link queue, de-duplicated by 8-hex event code
```

**Archive-then-parse** is deliberate. Raw HTML is kept forever; derived JSON is
disposable and regenerable. If RaceClocker changes its markup, history is not
lost — only the parser needs updating.

## 3. Discovery: three routes, in order of preference

### 3a. Admin event list (best — newly available)

`https://raceclocker.com/My_Events.php` behind your admin login enumerates
*every* event in the club account, including aquathons that may never have been
posted to the chat. This is strictly better than link-scraping: complete,
ordered, and not dependent on someone remembering to share a link.

**Not yet inspected** — the Claude Chrome extension is not currently connected,
and an authenticated page cannot be reached with `curl`. Next step is to look at
that page and see whether it exposes a JSON endpoint or a CSV export. I will not
be entering your credentials; you stay logged in and I read the
already-authenticated page.

### 3b. WhatsApp chat export (backfill)

WhatsApp's built-in *Export chat → Without media* produces a `.txt` with the
full history including every URL. Drop it in `data/exports/` and the bot
extracts all `raceclocker.com/<8-hex>` codes. Zero account risk, complete
history, one manual step.

### 3c. Linked device (ongoing, opt-in)

Pairs as a WhatsApp linked device to pick up new links automatically.

> **Risk, stated plainly:** this is unofficial automation of a personal account
> and carries a genuine ban risk. Two further limits matter: a newly linked
> device only syncs a limited window of history (which is exactly why 3b does
> the backfill), and the mature libraries (Baileys, whatsapp-web.js) are
> **Node-only** — Node is not currently installed on this machine. The Python
> option is `neonize` (bindings to `whatsmeow`).
>
> Mitigations: read-only listener, no auto-replies, no bulk sends, low request
> volume, and the bot never sends anything without explicit confirmation.

Given 3a is now available, **3c may be unnecessary** — worth deciding before
building it.

## 4. Race classification

Two series that must never share a trend line, because their results are not
comparable. Three independent signals are scored and cross-checked:

| Signal | Time trial | Aquathon |
|---|---|---|
| Weekday (from header text) | Tuesday | Thursday |
| Timed legs (populated split slots − 1) | 1 | 2 |
| Title keywords | `time trial`, `timetrial`, `tt` | `aquathon`, `aquathlon` |
| Start style (available, not yet scored) | individual starts | mass start |

**Validated against both sample events:** the TT classifies as `time_trial` and
the aquathon as `aquathon`, with all three signals agreeing in each case.

An event is **confident** only when at least 2 signals agree and none dissent.
Disagreement returns `unknown` with the conflict recorded, so it surfaces for
review rather than silently corrupting an athlete's history. This matters
because sessions get mislabelled, marshals miss splits, and events get moved off
their usual weekday — any single-signal classifier would quietly get those wrong.

Implemented in `ctc_bot/classify.py`; the disagreement path is tested.

## 5. Athlete identity — the hard problem

This is the part most likely to produce wrong-but-plausible charts.

In the sample event alone: `Kevin` and `Kevin G` are **two different people**;
`Peter` and `Peter M` likewise; `Dylan ` carries a trailing space. Bib numbers
are reassigned every event (1–15 here), so **bib is not identity**.

Design:

1. **Normalise** — trim, collapse whitespace, casefold for matching only
   (original spelling preserved for display).
2. **Curated alias map** (`aliases.yml`) — variant to canonical athlete.
   Hand-owned by you, version-controlled, the single source of truth.
3. **Fuzzy _suggestions_ only** — `rapidfuzz` proposes candidate merges for
   review. It must **never** auto-merge: on this data an automatic fuzzy pass
   would merge `Kevin`/`Kevin G` and be confidently wrong.
4. **Unresolved names are reported, never silently dropped**, so nobody quietly
   vanishes from their own trend line.

## 6. Metrics — making events actually comparable

Raw finish time across different courses, distances and weather is close to
meaningless as a trend. Store the raw value, but lead with normalised metrics:

- **Pace / speed** (min/km, km/h) — normalises distance.
- **Field-relative z-score** — `(athlete_time − field_mean) / field_stdev` for
  that event. Cancels out weather, wind and course changes, and is the single
  best "form" indicator for a recurring club series.
- **Rank percentile** — robust, easy to read, insensitive to outliers.
- **Personal best / season best** markers.
- **Per-leg splits** for aquathons — swim vs run legs trended separately, which
  is where the interesting coaching signal lives.

Trend lines are drawn **within a series** (TT with TT, aquathon with aquathon),
never pooled.

Handle `DNF` / `DNS` / penalties explicitly: excluded from trends, shown as
gaps, never coerced to zero.

## 7. Output

- **HTML dashboard** (`out/dashboard.html`) — self-contained, Jinja2-rendered,
  opened locally. Athlete filter, hover splits, head-to-head comparison.
- **On-demand PNG** (`matplotlib`) — one athlete's trend as a shareable image
  you can post to the group yourself.

## 8. Phases

| Phase | Deliverable | State |
|---|---|---|
| 0 | Scaffold, RaceClocker parser, pinned fixture tests, TT/aquathon classifier | **done** |
| 1 | WhatsApp export parser + link extraction + backfill runner | next |
| 1b | Admin `My_Events.php` discovery | blocked on browser |
| 2 | Athlete identity resolution + `aliases.yml` + unresolved-name report | |
| 3 | Metrics engine (pace, z-score, percentile, PB) | |
| 4 | HTML dashboard | |
| 5 | PNG renderer | |
| 6 | Aquathon per-leg analysis | |
| 7 | Linked-device listener (opt-in, only if 3a proves insufficient) | |

## 9. Open questions

1. **Does `My_Events.php` expose a clean export?** If yes, it likely replaces
   WhatsApp scraping entirely for discovery. Needs the Chrome extension connected.
2. **Is the TT course always 13 km?** If the route varies, distance must be
   captured per event (it already is) and trends grouped by course, not just series.
3. ~~Are aquathon legs swim-then-run, and is transition timed separately?~~
   **Answered** by `dd7293a5`: slots 1 → 2 → 5 give exactly two legs (swim, then
   run) with no separately timed transition — T1 is absorbed into the run leg.
4. **What are the aquathon leg distances?** The page publishes only a combined
   `8.0 km`, so swim and run pace cannot be computed until you supply the split
   (e.g. 750 m swim + 7.25 km run). Needed for per-leg pace; leg *times* already work.
5. **How far back does the chat history go**, and does it cover the full series?
6. **Is the aquathon always 8 km / the TT always 13 km?** If either route varies,
   trends must group by course as well as series.
