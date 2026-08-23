# CTC_bot — next steps

Written at a quota checkpoint. Nothing here is implemented; each item has enough
diagnosis to be picked up cold.

Live at `https://tri.jamescarron.cloud` (password `CTC2026`), 334 tests passing,
deployed image `778af9f`.

---

## 1. "Not mine" button not working — BUG

Reported live. Two candidates, and the first is confirmed to exist regardless.

### 1a. Class collision I introduced (confirmed by inspection)

`dashboard.py` has three buttons carrying `class="link-btn disown"`:

| Line | Button | Data attributes |
|---|---|---|
| 1011 | Remove a hand-added result | `data-a` (addition id) |
| 1022 | **Not mine** | `data-e`, `data-r` |
| 1067 | **Remove from this site** (opt-out) | *none* |

The binder at line 948 is:

```js
panel.querySelectorAll("button.disown:not(.remove-added)").forEach(b => {
  b.onclick = () => postRow("/api/disown", a, b.dataset.e, b.dataset.r, b);
});
```

That selector matches the opt-out button too, binding it to `/api/disown` with
`undefined` event and raceId. `wireOptOut` runs afterwards and overwrites
`onclick`, so opt-out currently works by luck of ordering.

**Fix:** give the actions their own classes rather than overloading `disown` as
both "styled red" and "is a disown control". Suggest `js-disown`, `js-remove-added`,
`js-optout` for behaviour, keeping `disown` purely cosmetic.

### 1b. It may be working but look broken

`postJson` reports success into `#row-msg` and says *"Refresh to see it applied."*
The row stays visible until reload, which reads as nothing happening.

**Fix:** on success, reload the page (or re-fetch the payload and re-render).
Given the dashboard is a single generated document, `location.reload()` is
honest and cheap.

**Diagnose first:** open devtools, click Not mine, check whether
`POST /api/disown` fires and what it returns. If it 404s with *"not claimed by
this athlete"*, the row was `inferred` rather than `claimed` and the button
should not have rendered — check `r.claimed` in the payload.

---

## 2. Drop the excluded-events list

The "How this is measured" section ends with a `<details>` listing all 57
excluded events. It was useful while curation was being tuned; it now just
invites questions about races nobody is looking for.

Remove the `<details>` block, `#exc-count`, `#excluded`, and the `excluded`
array from the payload (`build_payload`). Keep `curation` itself untouched —
only the display goes. Tests referencing `test_excluded_events_are_listed_with_reasons`
will need updating.

---

## 3. Remove the field-statistics wording

Already-dropped feature still advertised. The series subtitle says:

> "Field statistics are withheld where fewer than 5 people finished."

Nothing shows z-scores or percentiles any more — the "vs field" column went when
the athlete table was trimmed.

- Remove that sentence from `renderSeries`.
- Decide whether `z` / `pct` stay in the payload. They are still computed in
  `metrics.build` and carried per run, costing size for nothing. Recommend
  keeping the *computation* (item 5 needs the same field context) but dropping
  them from the payload.
- `curation.MIN_FIELD_FOR_STATS` stays — item 5 will want it.

---

## 4. Tune the aquathon plausibility bounds

Currently `min_speed_kmh=3.0`, `max_speed_kmh=14.0` over the combined 4.1 km, set
by eyeballing the distribution. The time trial's bounds were tuned properly
against real data; the aquathon's were not, which is part of why that tab still
carries a draft warning.

Real aquathon distribution measured earlier (n=747, before filtering):

| Band km/h | Count | Reading |
|---|---|---|
| 0–2 | 12 | timers left running (21–24 h) |
| 2–4 | 4 | ambiguous — 4.1 km at 3 km/h is 82 min |
| 4–6 | 44 | plausible, slow |
| 6–8 | 255 | the bulk |
| 8–10 | 363 | the bulk |
| 10–12 | 65 | fast |
| 12–14 | 2 | very fast |
| >14 | 2 | 359 km/h — the 40-second "race" |

**Approach:** the swim and run legs have very different plausible ranges, and a
combined figure hides both. Since `leg_seconds` is already parsed, bound each leg
separately — 600 m swim and 3.5 km run — which catches a plausible total made of
one impossible leg. That is the case the current single bound cannot see.

Sanity anchors from real data: winner's swim 11:27 (1:54/100 m), run 14:15
(4:04/km).

---

## 5. "How fast was tonight?" — conditions note

The most interesting item. Club times swing with wind and weather, and an
athlete seeing a slow time has no way to know whether it was them or the evening.

### Method

1. **Pick the regulars.** Athletes with enough results *in that series* to have a
   personal baseline — reuse `MIN_RACES_FOR_TREND` (3) or require more, say 5.
2. **Baseline each one on themselves**, not on the field: the *median* of their
   other results in that series. Median, not mean, so one bad night does not
   move their own yardstick.
3. **Ratio per athlete:** `tonight_speed / their_median_speed`. Above 1 = faster
   than they usually go.
4. **Combine with a median of the ratios**, again for robustness — one person
   having a shocker must not colour the whole evening.
5. **Only report it with enough regulars present.** Below ~5 it is one person's
   day rather than the conditions. Say nothing rather than guess.

### Reporting

> "Times were about **4% slower** than usual for the 11 regulars racing —
> a tough evening."

Bands roughly: >2% faster = fast conditions; ±2% = typical; >2% slower = slow;
>5% slower = hard.

### The trap to avoid

This confounds *conditions* with *form*. Using each athlete's own median handles
individual improvement, but not a season-wide trend — if the whole club is
fitter in August than March, August looks like "fast conditions" forever.

Mitigation: baseline against each athlete's *nearby* races (say ±60 days) rather
than their whole history. More code, but it measures the evening rather than the
season.

### Where it goes

The latest-race strip, as one sentence under the event title. It is also the
right place to put it for the race-night poll, so the note appears the moment
results land.

---

## Also outstanding (from earlier sessions)

- **CI still not running.** Every deploy is a manual build on the box. Needs
  `infra` → Settings → Actions → General → Access → *accessible from
  repositories owned by the user*, plus an `INFRA_REPO_TOKEN` secret on
  `CTC_bot`. Until then, deploys are: build on box, bump SHA in
  `apps/tri/compose.yml`, `docker compose up -d`.
- **Race-night polling is untested against a real race.** First live run is
  Tuesday 19:00. Worth watching `docker logs tri-app-1`.
- **RaceClocker session expiry** in a long-lived container. `session.py` detects
  being bounced to the login form but does not re-login; the sweep will fail and
  log until that retry is added.
- **Archive the Windows `identity.json` / `overrides.json`.** Backup is
  Hetzner snapshots only, 24 h granularity, no per-file history.
