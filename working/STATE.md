# CTC_bot — resumable state

Last updated: 19 Aug 2026. **187 tests passing.**

## How to run it

```
ctc dashboard        # build + serve + open browser  (or double-click dashboard.bat)
ctc backfill         # pull new events from the admin console
ctc claim -- James   # claim results by first name
ctc doctor           # check TLS, credentials, login, stored events
ctc courses          # show/change course distances
ctc events           # list stored events
ctc test             # run the suite
```

Environment: local **pixi** (`.pixi/`, gitignored; `pixi.lock` committed).
`pixi.exe` is checksum-verified at `C:\GitHub\Applets\tools\pixi.exe`.
`ctc.cmd` wraps it. Credentials: Windows DPAPI at
`%LOCALAPPDATA%\CTC_bot\credentials.bin` (logged in as timekeeping@corktri.com).

## Where things stand

**Done — phases 0–5.** Discovery, parsing, classification, curation, identity,
metrics, dashboard, local server, claim API, `.bat` launcher.

Data: **228 events listed → 209 fetched → 152 curated races** (105 time trials,
47 aquathons), **2,174 athlete performances**, **779 athletes**, 2019–2026.
Zero events needing review.

### Modules

| File | Does |
|---|---|
| `raceclocker.py` | Fetch/parse result pages; legs, elapsed, `ranked()` |
| `classify.py` | TT vs aquathon; admin `start_type` is authoritative |
| `curation.py` | Which events count; 57 excluded with reasons |
| `identity.py` | Claim-based identity, placeholders, verified flag |
| `config.py` | Course + **route** distances (admin-editable) |
| `discovery.py` | Parse `My_Events.php`, resolve index → public code |
| `store.py` | Archive raw HTML + parsed JSON; `reclassify_all()` |
| `metrics.py` | Performances, speed, z-score, percentile, PB, trend fit |
| `dashboard.py` | Self-contained HTML + embedded JSON + SVG charts |
| `server.py` | Localhost server + `/api/claim` |

### Decisions locked in

- **Two TT routes** — Short 13.0 km (60 events), Long 13.8 km (44). They overlap
  in 2022–25, so the club alternates; matched by advertised distance, not date.
- Aquathon 600 m swim + 3.5 km run. Page distances never trusted.
- Excluded: track/running series, one-off club events, untimed events, copies,
  templates, tests, empties.
- Field stats need ≥5 finishers; trend line needs ≥3 races in a series.
- Placeholders (`unknown` ×62, `name` ×17, bib numbers) count towards field
  size but never become athletes.
- Unclaimed athletes shown but marked **unverified**.
- Everyone listed; WhatsApp dropped as input (possible output later).

### Identity state

Only **James Carron** is claimed (6 races, spellings `james carron`,
`james carrons`). `James C` (9 Jun '26, 26:06.7) still unclaimed — your call.
`data/identity.json` is gitignored: **back it up**, losing it loses every claim.

## Next steps, highest value first

1. **Aquathon per-leg analysis.** Swim and run legs are already parsed
   (`rc.leg_seconds`) and in the payload as `legs`, but the dashboard does not
   chart them separately. This is where the coaching signal is — one athlete
   had the fastest run in the field while losing five minutes in the swim.
2. **Scheduled Wed/Fri refresh.** Decided but not built: a Windows scheduled
   task running `ctc backfill` then `ctc build`.
3. **Bulk claim UX.** 779 athletes, 1 claimed. The dashboard claim button takes
   a whole provisional group at once; a per-row picker would let someone split
   two people who share a name.

## Known gaps

- Charts are speed-over-time only; no z-score or percentile chart yet.
- Dashboard claim form claims a whole group; cannot split a contested name.
- 35 rows resolve as `contested` and need a person to disentangle them.
- Nothing verifies the route assumption against actual GPS — only advertised
  distance.
