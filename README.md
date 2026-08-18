# CTC_bot

Harvests RaceClocker links from the club triathlon WhatsApp chat, parses each
event's results, and renders per-athlete trend lines.

See [PLAN.md](PLAN.md) for architecture, verified findings and roadmap.

## Setup

```bash
python -m pip install -r requirements.txt
python -m pytest tests/ -q
```

## Current capability

```python
from ctc_bot import raceclocker as rc
from ctc_bot import classify as cls

event = rc.load("dd7293a5")
print(event.title, event.distance, event.distance_unit)   # Aquathon 18th June 8.0 km
print(event.segments)                                     # 2 timed legs
print(cls.classify(event).race_type)                      # aquathon

for row in rc.ranked(event.results)[:3]:                  # recomputed positions
    print(row["Position"], row["Name"], rc.leg_seconds(row))
```

## Layout

| Path | Purpose |
|---|---|
| `ctc_bot/raceclocker.py` | Fetch + parse RaceClocker result pages, leg/elapsed maths |
| `ctc_bot/classify.py` | Time trial vs aquathon classification |
| `tests/fixtures_*.html` | Verbatim snapshots of real events, used as regression fixtures |
| `data/raw/` | Archived HTML snapshots (gitignored) |
| `data/exports/` | WhatsApp chat exports (gitignored — personal data) |
| `out/` | Generated dashboard and PNGs (gitignored) |

## Gotchas worth knowing

These are all verified against real pages, not assumptions:

- **`Rank` is unreliable** — it mirrors bib order in both sample events. Use
  `rc.ranked()`, which recomputes position from `TmResultSec`.
- **`SplitNames` is a generic template** — the aquathon's real finish sits in a
  slot labelled `"Run start"` and the slot labelled `"Finish"` is empty. Legs are
  derived from which slots are populated.
- **Split times need their `...dc` deci-second field**, or results drift by up to
  0.9 s.
- **Bib numbers are reassigned every event**, so they are not athlete identity.
  Names are free text and collide meaningfully (`Kevin` and `Kevin G` are two
  different people) — identity is resolved from a curated alias map, never by
  automatic fuzzy matching.
