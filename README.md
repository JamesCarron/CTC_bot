# CTC_bot

Pulls the club's triathlon events from the RaceClocker admin console, parses
each event's results, and renders per-athlete trend lines.

Athletes identify themselves by claiming their own results: enter a first name,
pick your races out of the list, and the tool remembers you from then on.

See [PLAN.md](PLAN.md) for architecture, verified findings and roadmap.

## Setup

The project uses [pixi](https://pixi.sh) for a self-contained environment. It
lives in `.pixi/` inside this folder — nothing is installed system-wide, and
`pixi.lock` pins exact package versions.

```bash
ctc              # list the available commands
ctc test         # run the test suite
ctc doctor       # check TLS, credentials and stored events
```

`ctc.cmd` wraps pixi, so there is nothing to activate and no paths to remember.
The first command solves the environment (a few minutes); everything after is
instant.

| Command | Does |
|---|---|
| `ctc login` | Store the RaceClocker login (password hidden, DPAPI-encrypted) |
| `ctc check-login` | Verify the stored credentials by logging in |
| `ctc forget-login` | Remove the stored credentials |
| `ctc doctor` | Check TLS, credentials, login and stored events |
| `ctc events` | List stored events (`ctc events dd7293a5` for one field) |
| `ctc test` | Run the test suite |

<details>
<summary>Without pixi</summary>

```bash
python -m pip install -r requirements.txt
python -m pytest tests/ -q
```

</details>

## Logging in

The app authenticates to RaceClocker itself. No browser, no automation tooling,
no AI involved at runtime.

```bash
ctc login          # store the login (password typed hidden)
ctc check-login    # verify what is stored
ctc forget-login   # remove it
```

The password is typed without echo, encrypted with **Windows DPAPI**, and
written to `%LOCALAPPDATA%\CTC_bot\credentials.bin` — deliberately **outside
this repository**, so it can never be committed. DPAPI binds the ciphertext to
your Windows user account: copying the file to another machine or opening it as
another user yields nothing. There is no master password to remember, and no
plaintext fallback — if no secure backend is available the script refuses to
store anything.

Then, in code:

```python
from ctc_bot import session

http = session.login()                 # reads the encrypted store
html = session.fetch_event_list()      # authenticated My_Events.php
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
| `ctc_bot/identity.py` | Claim-based athlete identity: search, claim, resolve |
| `ctc_bot/config.py` | Course distances (admin-editable, overrides the page) |
| `ctc_bot/store.py` | Archive raw HTML + persist parsed events |
| `ctc_bot/credentials.py` | DPAPI-encrypted credential storage |
| `ctc_bot/session.py` | Authenticated RaceClocker session |
| `scripts/set_credentials.py` | Interactive, secure credential setup |
| `scripts/doctor.py` | Environment/credential/login diagnostics |
| `scripts/show_events.py` | Inspect stored events and results |
| `pixi.toml`, `ctc.cmd` | Local environment and launcher |
| `tests/fixtures_*.html` | Verbatim snapshots of real events, used as regression fixtures |
| `data/raw/`, `data/events/` | Archived snapshots and parsed events (gitignored) |
| `data/identity.json` | Athlete claims (gitignored — personal data; **back this up**) |
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
- **No demographic data exists.** `Gender` is `"Male"` for all 24 athletes across
  both events, including Kathleen, Fiona, Dee, Maura, Lorraine, Keelin and
  Sinead; `Age`, `Club`, `Cat` and `Wave` are empty or defaults. The name string
  is the only identifying field, which is why identity is claimed rather than
  inferred.
- **The published aquathon distance is wrong** — `8.0 km` against a real 4.1 km
  course. Distances come from `ctc_bot/config.py` / `data/courses.json`.
- **TLS on this machine needs `truststore`.** Something here inspects TLS and
  presents its own root CA, trusted by Windows but absent from certifi, so plain
  `requests` fails `CERTIFICATE_VERIFY_FAILED` on *every* host. `ctc_bot`
  redirects verification to the Windows certificate store on import.
  Verification stays fully enabled — never disable it instead.

## Claiming your results

```python
from ctc_bot import store, identity as idn

events = store.load_all()
registry = idn.Registry.load()

candidates = registry.search("Kevin", events)
for i, c in enumerate(candidates, 1):
    print(i, c.describe())
#  1  Tuesday 18 Aug '26  Timetrial 18th aug  'Kevin G'  bib 2   #1  20:12.8
#  2  Tuesday 18 Aug '26  Timetrial 18th aug  'Kevin'    bib 4  #13  25:20.2

registry.claim("Kevin Gallagher", [candidates[0]])   # ticks row 1 only
registry.save()
```

From then on, `Kevin G` resolves to Kevin Gallagher automatically in future
events, while `Kevin` stays a separate person.
