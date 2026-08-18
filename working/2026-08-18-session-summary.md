# CTC_bot — session summary, 18 Aug 2026

## Goal

Plan a WhatsApp bot that finds RaceClocker links in the club triathlon chat,
parses the results, and builds per-athlete trend lines. Create the applet
folder, init a git repo, commit as features land.

## What was done

Created `C:\GitHub\Applets\CTC_bot`, initialised git, and landed 4 commits.
Rather than only planning, I de-risked the core assumption by parsing two real
events end to end.

| Commit | Contents |
|---|---|
| `bf0c53e` | Project scaffold, gitignore, requirements |
| `872bffd` | RaceClocker parser + two archived page fixtures |
| `3c41e88` | TT/aquathon classifier + 19 tests (all passing) |
| `c4dbd0c` | `PLAN.md` and `README.md` |

## Key findings (verified against live pages, not assumed)

Sample events: `7eecd645` (Timetrial 18th Aug, 13 km, 15 athletes) and
`dd7293a5` (Aquathon 18th June, 8 km, 9 athletes).

1. **Scraping is easy.** RaceClocker renders server-side and embeds the whole
   field as a `let AllResults = [...]` JSON array. No headless browser, no DOM
   scraping — a `requests` GET plus `json.loads`. This was the main project risk
   and it is gone.
2. **`Rank` is unreliable.** It mirrors bib order in *both* events (Gedis is
   "Rank 1" at 21:14 while Kevin G ran 20:12). Position is now recomputed from
   `TmResultSec`.
3. **`SplitNames` is a generic triathlon template, not ground truth.** The
   aquathon's real finish sits in the slot labelled `"Run start"`; the slot
   labelled `"Finish"` is empty. Legs are derived from *populated* slots.
4. **Deci-seconds matter.** Each split has a companion `...dc` field. Including
   it, computed elapsed reconciles with RaceClocker's own total for **24/24
   athletes across both events**. Ignoring it drifts results by up to 0.9 s.
5. **TT = individual starts (15 distinct); aquathon = mass start (1 shared).**
6. **No per-leg distances are published**, so swim/run *pace* needs config. Leg
   *times* already work (winner: 11:28 swim, 14:15 run).

## Classification

Cross-checks three signals — weekday (Tue = TT, Thu = aquathon), timed legs
(1 vs 2), and title keywords. Both events classify correctly with all three
agreeing. Disagreement returns `unknown` for review rather than guessing, since
sessions get mislabelled and splits get missed.

Note: the initial brief said both series run Tuesdays, which would have made
weekday useless; corrected mid-session to Thursday for aquathons.

## Decisions taken

- **WhatsApp access:** hybrid — export for backfill, linked device for ongoing.
- **Output:** HTML dashboard plus on-demand PNG.
- **Stack:** Python only. Node is not installed, which rules out Baileys and
  whatsapp-web.js for the linked-device phase (`neonize` is the Python option).

## Blocked / next

- **Admin route not yet inspected.** `My_Events.php` would enumerate every event
  in the club account and likely replace WhatsApp link-scraping for discovery.
  Blocked: the Claude Chrome extension is not connected, and an authenticated
  page cannot be fetched with `curl`.
- Open questions are listed in `PLAN.md` §9 — chiefly the aquathon leg distance
  split and whether the courses are fixed.
- Next build phase is the WhatsApp export parser + backfill runner.
