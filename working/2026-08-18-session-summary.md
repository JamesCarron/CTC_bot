# CTC_bot — session summary, 18 Aug 2026

## Goal

A bot that pulls the club's RaceClocker events, parses results, and builds
per-athlete trend lines. Create the applet folder, init git, commit as features land.

## State

`C:\GitHub\Applets\CTC_bot` — 9 commits, 46 tests passing, clean tree.

| Commit | Contents |
|---|---|
| `bf0c53e` | Project scaffold |
| `872bffd` | RaceClocker parser + two archived page fixtures |
| `3c41e88` | TT/aquathon classifier |
| `c4dbd0c` | `PLAN.md` / `README.md` |
| `94afdfe` | Session summary |
| `d0de521` | Event store (archive raw HTML + parsed JSON) |
| `2da9f1b` | Course config, admin-editable distances |
| `eb82f5e` | Claim-based athlete identity |
| `35c0e93` | Drop WhatsApp as input; document decisions |

## Scope change

**WhatsApp is no longer an input source.** The RaceClocker admin console
(`My_Events.php`) is the sole source of truth. This also removed the ban risk,
the Node dependency and the limited-history problem. WhatsApp may return later
as an *output* channel only.

## Key findings (verified against two live pages, not assumed)

Sample events: `7eecd645` (Timetrial 18 Aug, 13 km, 15 athletes) and
`dd7293a5` (Aquathon 18 Jun, 9 athletes).

1. **Scraping is easy.** Results are server-rendered into a `let AllResults = [...]`
   JSON array. No headless browser, no DOM scraping. Main project risk, eliminated.
2. **`Rank` is unreliable** — mirrors bib order in *both* events. Position is
   recomputed from `TmResultSec`.
3. **`SplitNames` is a generic template.** The aquathon's real finish sits in a
   slot labelled `"Run start"`; the slot labelled `"Finish"` is empty. Legs are
   derived from populated slots.
4. **Deci-seconds matter.** Including the `...dc` field, computed elapsed
   reconciles with the official total for **24/24 athletes across both events**.
5. **No demographic data exists.** `Gender` is `"Male"` for all 24 athletes,
   including Kathleen, Fiona, Dee, Maura, Lorraine, Keelin, Sinead. `Age`,
   `Club`, `Cat`, `Wave` empty/defaults. The name string is the only identifying
   field — which is *why* claim-based identity is the only sound option.
6. **The published aquathon distance is wrong** — `8.0 km` vs a real 4.1 km
   course. Confirmed by the user's 600 m + 3.5 km figures, which reconcile with
   the leg times (1:54/100 m swim, 4:04/km run). Distances now come from config.

## Identity design (implemented)

Claims are recorded against a **specific result row** (`event + RaceID`), never a
name string. Name variants are derived from claims. Resolution precedence:
`claimed` → `inferred` → `ambiguous` → `contested` → `provisional`.

This is what lets `Kevin` and `Kevin G` be two different people, lets a later
event resolve with no new input, and refuses to guess when a spelling maps to
two claimed athletes. Fuzzy merging deliberately not used — it would merge the
two Kevins and be confidently wrong.

Verified end to end: two Kevins claimed separately, John Doyle claimed across
both series, persisted to disk and reloaded intact.

## Decisions taken (from interview)

- Aquathon: 600 m swim + 3.5 km run, admin-editable.
- Both courses fixed across a season.
- Claim flow: web form in the dashboard.
- Visibility: everyone sees everyone.
- Trends: one rolling line over all history, seasons colour-coded.
- Unclaimed athletes: auto-group by exact name, flag collisions.
- Refresh: scheduled Wednesday and Friday mornings.
- Dashboard: all four headline views (latest strip, trend, vs-average, standings).

## Blocked

**Admin console not yet inspected.** An authenticated page cannot be fetched
with `curl`, and the Claude Chrome extension is not connected. To unblock:
connect it at `claude.ai/chrome` while staying logged in. I will not be entering
credentials.

Needed from that page: link format, whether a JSON/CSV export exists, and how
far back history runs.

## Next

Phase 4 — metrics engine (pace, z-score, percentile, PB), then the dashboard
with the claim form.
