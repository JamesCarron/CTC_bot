# CTC_bot — Plan

Pulls the club's events from the RaceClocker admin console, parses each event's
results, and renders per-athlete trend lines.

Status: **Phases 0–3 complete.** The club's full history is backfilled from the
admin console: **209 events pulled, 152 curated races** (105 time trials, 47
aquathons) spanning 2019–2026, **2,649 result rows**, zero events left needing
review. Claim-based identity works and persists. 110 tests passing.

> **Scope change (18 Aug):** WhatsApp is **no longer an input source**. The
> RaceClocker admin console is the sole source of truth for discovering events.
> WhatsApp may return later as an *output* channel only (posting results or
> charts to the chat), which carries none of the history-access problems that
> made the input route awkward.

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
| This machine sits behind a TLS-inspecting proxy presenting its own root CA - trusted by Windows, absent from certifi | Plain `requests` fails `CERTIFICATE_VERIFY_FAILED` on *every* host, not just RaceClocker. `ctc_bot` uses `truststore` to verify against the Windows certificate store. Verification stays on. |
| The page also loads Ably websockets for live updates | Live in-race tracking is possible later, but is explicitly out of scope for v1. |

Both sample events are archived as test fixtures, so any future parser change is
checked against real pages rather than assumptions.

## 2. Pipeline

```
  discovery              parse              identity            analyse           render
+--------------+  +------------------+  +--------------+  +--------------+  +--------------+
| RaceClocker  |  | raceclocker.py   |  | identity.py  |  | metrics.py   |  | HTML board   |
| admin console|->|  fetch + parse   |->| claim-based  |->| per-athlete  |->| + claim form |
| (My_Events)  |  | classify.py      |  | registry     |  | series       |  | + PNG export |
+--------------+  +------------------+  +--------------+  +--------------+  +--------------+
                          |                     |                 |
              data/raw/<code>.html      data/identity.json   data/courses.json
              data/events/<code>.json   (claims, remembered) (admin distances)
```

**Archive-then-parse** is deliberate. Raw HTML is kept forever; derived JSON is
disposable and regenerable. If RaceClocker changes its markup, history is not
lost — only the parser needs updating.

## 3. Discovery: the admin console

`https://raceclocker.com/My_Events.php`, behind your admin login, is the **sole
source of truth**. It enumerates every event in the club account — including
aquathons that were never shared to any chat — which makes it complete, ordered,
and independent of anyone remembering to post a link.

**Login is solved and needs no browser or AI.** The admin login is a plain HTML
form post - no CSRF token, no hidden fields, no JavaScript step:

```
POST https://raceclocker.com/Login.php
    fld_email=...&fld_password=...
```

Auth is carried by cookies thereafter, so one `requests.Session` covers a whole
run. Credentials are stored encrypted with Windows DPAPI outside the repo; see
`ctc_bot/credentials.py` and `scripts/set_credentials.py`.

**Still to inspect:** the structure of the event list itself. I have not seen
the authenticated page, so the listing parser is not written yet. Either connect
the Chrome extension, or simply run the app once logged in and share what the
page contains.

What is needed from that page:

- how events are listed (link format, whether the 8-hex code appears directly);
- whether a JSON endpoint or CSV export exists — much preferable to scraping;
- whether past seasons are paginated, and how far back history runs.

`raceclocker.extract_event_codes()` already pulls `raceclocker.com/<8-hex>`
codes out of any HTML, so a plain listing page needs no new parsing work.

### What the backfill found

228 events listed, 209 fetched (19 were never published to a public link). Of
those, **57 are not races the club would trend** and are excluded with a
recorded reason:

| Reason | Count | What it is |
|---|---|---|
| no recorded times | 11 | Start lists nobody ever timed - full fields, no results |
| track/running series | 11 | The Wednesday 3 km/5 km track TTs - a different discipline |
| copy | 10 | `(Copy of) ...` duplicates with overlapping fields |
| no participants | 9 | Empty shells |
| one-off club event | 8 | LaGrandeCourse, 5k/10k club races, training and away races |
| test | 5 | `TT Test Demo`, `test Aquathon` - made-up times |
| not the club TT course | 2 | A "5km Time Trial" is the running series, not the 13 km bike TT |
| template | 1 | `Aquathon TEMPLATE COPY ME` |

A shared date is **never** grounds for exclusion on its own: genuinely distinct
races share dates (a 5 km and a 10 km on one morning, the 20 km and 40 km legs
of one event). Only the event's own title, field and timing are used.

### WhatsApp: dropped as an input

Previously planned as the discovery route via chat export and a linked device.
Removed — the admin console is strictly better, and dropping it also removes the
account-ban risk, the Node dependency, and the limited-history problem that came
with linking a device.

WhatsApp remains a candidate **output** channel later (posting a results summary
or a chart to the group). That direction is far simpler: it needs no chat
history, and sending would always be gated on explicit confirmation.

## 4. Race classification

Two series that must never share a trend line, because their results are not
comparable. Three independent signals are scored and cross-checked:

| Signal | Time trial | Aquathon |
|---|---|---|
| **Admin console start type** (authoritative) | `Time trial` | `Mass start (1 split)` |
| Weekday (from header text) | Tuesday | Thursday |
| Timed legs (populated split slots − 1) | 1 | 2 |
| Title keywords | `time trial`, `timetrial`, `tt` | `aquathon`, `aquathlon` |

The admin console **states** each event's start type and split count. That is
recorded by the timing system rather than inferred from results, so when present
it settles the classification outright — any disagreeing inference is noted but
overruled. This resolved every one of the 34 events that inference alone left
uncertain, including a genuine aquathon that ran on a Tuesday (weekday said time
trial, structure said aquathon, so inference deadlocked at `unknown`).

**Validated against both sample events:** the TT classifies as `time_trial` and
the aquathon as `aquathon`, with all three signals agreeing in each case.

An event is **confident** only when at least 2 signals agree and none dissent.
Disagreement returns `unknown` with the conflict recorded, so it surfaces for
review rather than silently corrupting an athlete's history. This matters
because sessions get mislabelled, marshals miss splits, and events get moved off
their usual weekday — any single-signal classifier would quietly get those wrong.

Implemented in `ctc_bot/classify.py`; the disagreement path is tested.

## 5. Athlete identity — claim-based *(implemented)*

The part most likely to produce wrong-but-plausible charts, and the reason it
cannot be automated: **RaceClocker publishes no usable identifying data.**

Across both sample events, `Gender` is `"Male"` for all 24 athletes — including
Kathleen, Fiona, Dee, Maura, Lorraine, Keelin and Sinead. `Age`, `Club`, `Cat`,
`Wave`, `Handicap` and `Penalty` are all empty or defaults. The **name string is
the only identifying field that exists**, and bib numbers are reassigned every
event.

Meanwhile the names genuinely collide: `Kevin` and `Kevin G` are two different
people in the same race, as are `Peter` and `Peter M`; `Dylan ` carries a
trailing space.

So identity is **claimed, not inferred**:

1. An athlete enters their first name.
2. They are shown every matching result across all events — date, race, bib,
   position and time — enough to recognise their own races.
3. They tick the ones that are theirs.
4. The claim is stored against the **specific result row** (`event code + RaceID`),
   never against a name string. A name is ambiguous; a row is not.
5. Name variants are then *derived* from those claims, which is what lets two
   Kevins coexist without either corrupting the other's history.

### Resolution precedence

Every result row across every event resolves to exactly one of:

| Source | Meaning |
|---|---|
| `claimed` | The athlete ticked this exact row. Always wins. |
| `inferred` | The spelling was learned from exactly one athlete's claims — later events resolve with no new input. This is the "remembers going forward" behaviour. |
| `ambiguous` | The spelling belongs to two or more claimed athletes. Never guessed; surfaced for a claim. |
| `contested` | The same name appears twice **within a single event** — two people sharing it, or one person entered twice. The club's history has both, so neither is assumed. |
| `placeholder` | Not a person at all: `unknown` (62 rows), `name` (17), bare bib numbers, `test`. Never becomes an athlete, but still counts towards field size so z-scores reflect who actually raced. |
| `provisional` | Unclaimed, but the spelling is unique, so grouped by exact name. Unclaimed athletes still get a trend line. |

Nobody is ever silently dropped — a test asserts every row resolves to something.

Fuzzy matching is deliberately **not** used for merging. On this data an
automatic fuzzy pass would merge `Kevin`/`Kevin G` and be confidently wrong.

## 6. Metrics

Both courses are confirmed **fixed** across a season (TT 13 km; aquathon 600 m
swim + 3.5 km run), so raw time is directly comparable within a series. Trends
are still built on normalised metrics so that a future course change does not
invalidate history:

- **Raw time** — the headline number, valid because courses are fixed.
- **Pace / speed** — min/km and km/h, per leg for aquathons.
- **Field-relative z-score** — `(athlete − field mean) / field stdev` for that
  event. Cancels out weather and wind, which matter a lot for a 13 km bike TT.
- **Rank percentile** — robust and easy to read.
- **Personal best** markers.

Distances come from `data/courses.json` (admin-editable), **not** from the page.

- The aquathon page reports `8.0 km` against a real 4.1 km course (~2x error).
- **The time trial runs on two routes.** Listed distances form two clean
  clusters — 13.0–13.1 km (60 events) and 13.5–14.0 km (44 events) — and the two
  overlap in 2022–2025, so the club alternates between courses rather than
  having remeasured one. An event is matched to its route by the distance it
  advertises; within a route the wobble (13.0 vs 13.1) is flattened, but the 6%
  gap between routes is preserved. Trends can then mark a course switch instead
  of reading it as a change in form.

**Who gets a trend line:** everyone appears and is searchable, but a fitted line
needs `MIN_RACES_FOR_TREND` (3) races. Of 866 athlete groups, 251 clear that bar.

**Unverified identities:** a provisional group is only "everyone who typed this
exact name". Five of the club's most active entries are bare first names — Maura
(28 races), John (22), Tim (21), Kevin (21), Dee (20) — any of which may be more
than one person over seven years. These are shown, but marked unverified rather
than presented as fact.

Trends are drawn **within a series**, never pooled. `DNF`/`DNS` are excluded
from trends and shown as gaps, never coerced to zero.

**Time bucketing:** one continuous rolling line per athlete across all history,
with seasons colour-coded so both the long arc and within-season form are visible.

## 7. Output — the dashboard

Single self-contained HTML dashboard, everyone sees everyone. All four views,
in this order:

1. **Latest result strip** — the most recent event, for immediate post-race interest.
2. **"Am I improving?"** — per-athlete trend line with fitted direction and PB markers.
3. **"How did the last race go?"** — each athlete's change vs their own average.
4. **Club standings** — season leaderboard across the series.

Plus:

- **Claim form** — the identity flow above, served in the dashboard so athletes
  self-serve. Needs a small local web server rather than a plain `file://` page,
  since claims write back to `data/identity.json`.
- **On-demand PNG** — one athlete's trend as a shareable image.

## 8. Automation

Scheduled runs on **Wednesday and Friday mornings**, the mornings after the
Tuesday TT and Thursday aquathon. Each run pulls the admin event list, ingests
anything new, re-resolves identity and rebuilds the dashboard.

## 9. Phases

| Phase | Deliverable | State |
|---|---|---|
| 0 | Scaffold, RaceClocker parser, pinned fixture tests | **done** |
| 1 | TT/aquathon classifier | **done** |
| 2 | Claim-based identity registry, search/claim/resolve, persistence | **done** |
| 2b | Course config, admin-editable distances | **done** |
| 3a | Secure credential storage + authenticated session | **done** |
| 3b | Admin console event-list parsing + full backfill | **done** |
| 3c | Event curation (which events count towards trends) | **done** |
| 4 | Metrics engine (pace, z-score, percentile, PB) | **next** |
| 5 | HTML dashboard + claim web form | |
| 6 | PNG renderer | |
| 7 | Scheduled Wed/Fri refresh | |
| 8 | WhatsApp as an *output* channel (optional, later) | |

## 10. Decisions taken on the backfilled data

- **Track/running TTs excluded.** The Wednesday 3 km/5 km track series is a real
  series, but a 5 km run cannot share a trend line with a 13 km bike time trial.
- **One-off club events excluded.** LaGrandeCourse, the 5k/10k club races,
  training and away races happen once, so there is nothing to trend against.
- **Untimed events excluded**, with the reason recorded.
- **Small fields kept, field statistics suppressed.** Races with fewer than
  `MIN_FIELD_FOR_STATS` (5) finishers still show raw times and pace; z-score and
  percentile are withheld, because a z-score from a 2-person field is noise
  presented as signal. Seven races are affected.

## 11. Remaining questions

1. ~~The TT course is not quite fixed.~~ **Decided: the time trial is 13 km.**
   Listed distances across the 105 time trials drift between 13.0 and 14.0 km
   (13.0, 13.1, 13.5, 13.6, 13.62, 13.8, 13.9, 14.0) — almost certainly the same
   road measured by different devices over seven years, not eight courses. A
   single configured distance is used for every event of that type, so no
   phantom variation enters the trends. Admin-changeable via `ctc courses`.

   Still worth confirming eventually whether the route ever genuinely changed —
   if it did, those events would need grouping separately.
2. **Do athletes ever change their entered name** between seasons (e.g. `Kev` one
   year, `Kevin G` the next)? Claims handle it, but it affects how much manual
   claiming is needed up front.
3. **Should the claim form be reachable by other club members**, or does it only
   ever run on your machine? Affects whether the local server needs any access
   control at all.
