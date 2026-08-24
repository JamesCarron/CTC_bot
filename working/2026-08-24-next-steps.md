# CTC_bot — next steps

Written at a quota checkpoint on 2026-08-24. **All five items were implemented on
2026-08-25** — this file now records what was done and what is still outstanding.

Live at `https://tri.jamescarron.cloud` (password `CTC2026`), 348 tests passing.
Deployed 2026-08-25 as `6403c6f`, infra `f18e31e`.

**Deploy, as actually done** (CI is still not running, so the image is built on
the box):

```bash
ssh -i ~/.ssh/id_ed25519_hetzner admin@100.68.148.7   # Tailscale IP; :22 is closed on the public IP
cd /opt/apps-src/CTC_bot && git pull --ff-only
docker build -t ghcr.io/jamescarron/tri:<full-sha> .
# then locally: bump the tag in infra/apps/tri/compose.yml, commit, push
git -C /opt/infra pull --ff-only
cd /opt/infra/apps/tri && docker compose -p tri --env-file /opt/infra/config.env up -d --no-build
```

`--env-file /opt/infra/config.env` supplies `DOMAIN` for the Traefik host rule;
without it the router rule comes out as ``Host(`tri.`)`` and the site stops
resolving. `-p tri` matches the running project name.

---

## 1. "Not mine" button — DONE

Two causes, both real.

### 1a. Class collision (confirmed live)

`disown` was doing two jobs: "styled red" and "is a disown control". Three
buttons wore it — remove-a-hand-added-result, Not mine, and the opt-out button
added in the previous deploy. The binder selected
`button.disown:not(.remove-added)`, which matched **16** buttons on James
Carron's panel when only 15 were Not-mine. The extra one was opt-out, bound to
`/api/disown` with `undefined` ids; `wireOptOut` overwrote it afterwards, so
opting out worked purely by luck of ordering.

**Fixed** by splitting behaviour classes from cosmetic ones: `js-disown`,
`js-optout`, `js-remove-added`, `js-edit-time`, `js-reset-time`, `js-adopt`.
`disown` is now styling only. Verified in the browser: the new selector matches
exactly 15.

### 1b. It worked, and looked like it hadn't

`/api/disown` was fine all along — calling `disown_row` directly returned
*"Released back to 'James', as printed on the entry list."* The problem was
feedback. `#row-msg` lives at the foot of the athlete panel, inside the "Add a
result that is missing" box. A Not-mine click a screenful above it printed
"Refresh to see it applied" off-screen and left the row exactly where it was.

**Fixed**: every row action now reloads the page on success, so the change is
the feedback. Failures never reload and are scrolled into view instead.

To stop the reload dumping people back at the top, the tab, athlete and season
now ride in the URL fragment (`#s=…&a=…&y=…`) — which also makes an athlete
linkable. Verified end to end: clicked Not mine, page reloaded, James Carron
still selected on the same season, disown buttons 15 → 14.

### 1c. The subtler trap

Releasing a row whose entry name is a spelling of your own puts it straight back
by *inference* — it stays on the page, now marked "by name" instead of claimed.
That is correct, but it looks like nothing happened. The button's tooltip now
says so.

## 2. Excluded-events list — DONE

`<details>`, `#exc-count`, `#excluded` and the `excluded` array are gone.
`summary.excluded` keeps the count. `curation` is untouched.

## 3. Field-statistics wording — DONE

The series subtitle is now just the year range. `z` and `pct` are out of the
payload (the page is 732 KB → 699 KB), though still computed — item 5 needs the
same field context. The chart tooltip dropped its z-score, and a hand-added
result now reads "no recorded field" rather than "#0 of 0".
`curation.MIN_FIELD_FOR_STATS` stays.

## 4. Aquathon plausibility bounds — DONE, per leg

The combined 3–14 km/h bound turned out to reject **nothing** among real
finishers in included events — all 283 rejections were `TmResultSec <= 0`, i.e.
DNS/DNF. Real finishers run 3.9–12.4 km/h, comfortably inside. So the combined
bound was not the problem; the missing check was per-leg.

`Leg` now carries its own bounds, and `Course.is_plausible` checks the splits
when they line up with the configured legs:

| Leg | km | min | max | max means |
|---|---|---|---|---|
| Swim | 0.6 | 0.9 | 6.5 | 600 m in 5:32 — quicker than the 800 m free world record |
| Run | 3.5 | 3.0 | 19.0 | 3.5 km in 11:03 — quicker than anyone in the club |

Measured from 691 real splits: swim 1.1–9.6 km/h (median 2.9), run 5.5–24.9
(median 12.3).

**Caught 5 results** whose totals looked perfectly ordinary but whose splits
cannot both be real — e.g. a 19:40 swim paired with an 8:26 run for 3.5 km,
faster than the world record. No event's curation verdict changed (148 included
/ 61 excluded, before and after).

The combined bound stays at 3–14. Widening the floor to 2.5 would readmit
exactly two 97-minute results that the earlier notes already flagged as
ambiguous; there is no evidence either way, so it was left alone.

An untimed or differently-shaped split set is not judged — a missing split is
not evidence of a bad one. Configs written before leg bounds existed fall back
to the built-in bounds, not to "no limit".

## 5. "How fast was tonight?" — DONE

`metrics.race_conditions(athletes, series, event_code)` returns a `Conditions`
or `None`. Each regular is compared with the **median of their own nearby
results**, and the ratios combined with another median.

- `CONDITIONS_WINDOW_DAYS = 60` — the trap the earlier notes identified. Using a
  whole history measures form as much as weather; a club fitter in August than
  in March would make every August read as fast conditions for ever. ±60 days is
  short enough that fitness is flat and long enough that a weekly race fills it.
- `CONDITIONS_MIN_BASELINE = 3` nearby races before an athlete counts.
- `CONDITIONS_MIN_REGULARS = 5` — below that, say nothing at all.

Bands: ≥+2.5% fast, ≥+1% quick, ±1% typical, −2.5% slow, below that hard. Taken
from the spread actually observed (−3% to +4% across recent time trials), so the
5% band the earlier notes suggested would never have fired.

Shown as one sentence under the latest-race title:

> **An ordinary evening.** The 9 regulars racing went about as fast as they
> usually do.

The aquathon prints nothing — too few consolidated identities to have five
regulars, which is the honest answer and matches the draft warning on that tab.

---

---

## Second batch — 2026-08-25, after an interview

### Sweep cached transient failures as "never published" — FIXED

`sweep()` did `cache.record(listing, None)` on *any* exception. A timeout, a
500 or a mid-sweep sign-out was recorded identically to "this event has no
public page", and `ListingCache.lookup` then skipped that listing for
`UNPUBLISHED_RETRY_DAYS = 30`. One bad sweep could hide a genuinely new race
for a month.

Now: `LookupError` (the definitive "no public code on the page") still caches;
a `LoginError` stops the sweep early, since every remaining request would fail
the same way; anything else counts as failed and caches nothing, so the next
sweep retries it. `tests/test_scheduler.py` covers all three, and two of them
fail against the old code.

**The documented session-expiry worry was overstated.** `poll_tonight` and
`sweep` each call `session.login()` fresh (`scheduler.py:113`, `:151`), so a
container running for weeks never reuses a stale session between runs. Only
mid-run expiry was ever a risk, and that is what the early stop handles.

### Merge suggestions — BUILT (`ctc_bot/merge.py`)

Proposes spellings that look like one person; a human confirms. Nothing merges
itself. Over the club's real data: **129 suggestions collapsing 184 spellings**,
76 on strong evidence.

Rules, each earned from a real failure:

- **First name must match exactly.** Matching loosely on both names at once is
  how *Colin Feeley* reaches *Colm Feely*.
- **Two spellings in the same race are never proposed.** Nobody races twice, so
  co-occurrence is proof of two people. Rejects 12 plausible-looking pairs.
- **Full names are grouped first, abbreviations attached second, and only when
  exactly one group could take them.** The first version chained *John O* to
  O'Connell, O'Driscoll and O'Shaughnessy at once and reported three men as one
  person with 103 races. It also absorbed *Peter Martin* into *Peter Meaney* and
  *Mark MacManus* into *Mark McEntee*.
- **Apostrophes and accents are folded for comparison only** — 21 names carry a
  curly apostrophe and 12 of those also exist with a straight one; three arrive
  with the fada as a combining accent. The folding is *not* pushed into
  `identity.normalise`, because that would silently re-group athletes without
  anybody confirming anything.

Endpoints: `GET /api/merge-suggestions`, `POST /api/merge`,
`POST /api/dismiss-merge` (both writes are in `_WRITE_PATHS`, so `CTC_READ_ONLY`
covers them). Dismissals persist in `identity.json`. Applying uses the ordinary
claim machinery, so a merge is a batch of normal claims and is undone the same
way — one **Not mine** at a time.

**Note before this goes live:** anyone with the site password can merge
identities in bulk. That is consistent with the honour system the intro already
describes, but it is a bigger lever than a single claim.

### Decisions taken

- **CI: not doing it.** Deploys stay manual — build on the box, bump the SHA,
  `docker compose up -d`. Procedure recorded above.
- **Backups: settled.** Hetzner backups are enabled and considered sufficient.
  Worth remembering that these are whole-VM snapshots, so recovering one file
  means rolling the box back or mounting the snapshot; there is no per-file
  history.

---

## Still outstanding

- **Race-night polling is untested against a real race.** First live run is
  Tuesday 19:00. Worth watching `docker logs tri-app-1`.
- **Aquathon identities are still unconsolidated** — the tooling now exists, but
  nobody has worked through the 129 suggestions. Doing so should retire the
  draft warning and give that tab enough regulars for a conditions note.
- **This second batch is not deployed.**
