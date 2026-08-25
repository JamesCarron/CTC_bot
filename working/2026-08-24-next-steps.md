# CTC_bot — state and next steps

Last updated **2026-08-25**. Started as a quota-checkpoint list; now a running
record of where the project stands and what is left.

| | |
|---|---|
| Club site | `https://tri.jamescarron.cloud` — password `CTC2026` |
| Admin tools | `https://tri-admin.jamescarron.cloud` — same password, separate cookie |
| Deployed | CTC_bot `fda187f`, infra `1ffc107` |
| Tests | 392 passing |
| Registry | 136 confirmed athletes, 1,265 claims, 475 listed, 119 verified |

---

## Deploying

**CI is not running and is not going to be** — deploys are manual by decision,
not by neglect. The image is built on the box because GitHub Actions never got
the two settings changes it needed.

```bash
# SSH only works over Tailscale. Port 22 is refused on the public IP that
# ~/.ssh/config points `apps-01` at, so that config entry does not work as-is.
ssh -i ~/.ssh/id_ed25519_hetzner admin@100.68.148.7

cd /opt/apps-src/CTC_bot && git pull --ff-only origin main
docker build -t ghcr.io/jamescarron/tri:<full-sha> .

# then locally: bump the tag in infra/apps/tri/compose.yml, commit, push
git -C /opt/infra pull --ff-only
cd /opt/infra/apps/tri
docker compose -p tri --env-file /opt/infra/config.env up -d --no-build
```

Two things that bite if forgotten:

- **`--env-file /opt/infra/config.env`** supplies `DOMAIN`. Without it the
  Traefik rule comes out as ``Host(`tri.`)`` and the site stops resolving.
- **`-p tri`** matches the running project name. A different one creates a
  second stack against an empty volume.

Arcane's git-watch pulls the infra commit but cannot deploy it: it expects the
image in GHCR, and a locally built tag is not there. Hence the manual compose.

---

## Things worth knowing that the code does not say

**Racing runs May to September.** In-season gaps are 7 days (time trial) and 14
(aquathon); winter gaps run 238–406. That is why the chart breaks its axis
between seasons, and why an all-time view on a plain timeline was two thirds
empty.

**The aquathon will never carry a conditions note, and that is correct.** The
last race had 12 people in it, six of whom had never raced another aquathon
within two months. 197 people have raced exactly one aquathon and never come
back; only 51 have four or more. Even a ±365-day window yields 3 regulars
against a threshold of 5. This was assumed to be a name-consolidation problem
and was not — consolidating the names changed nothing here.

**A released claim comes straight back by inference.** The most
counter-intuitive thing in the identity model. Releasing a claim on a row
entered as *James* does nothing durable if the athlete claims that spelling
elsewhere, because `resolve()` re-attaches it on the next build. `Registry`
therefore keeps an **exclusion** record, which inference respects and a fresh
claim clears. Anything touching `resolve()` must keep that ordering.

**Merge suggestions never merge.** `merge.py` proposes; a person confirms. Its
rules are all scar tissue — the first version chained *John O* to O'Connell,
O'Driscoll and O'Shaughnessy and reported three men as one person with 103
races. Full names are grouped first, abbreviations attached only where exactly
one group can take them.

**The admin subdomain is not a secret.** Traefik takes a certificate per router,
so `tri-admin.jamescarron.cloud` is in public Certificate Transparency logs.
The separation is enforced by the server — `CTC_ADMIN_HOST` gates the merge
endpoints on the `Host` header and they 404 on the club site — but the password
is the same one, so anyone who can see the club site can use the tools if they
find the address.

**Names carry curly apostrophes and combining accents.** 21 names use U+2019 and
12 of those also exist with a straight quote; three arrive with the fada
decomposed. `merge.py` folds both for comparison only — deliberately *not* in
`identity.normalise`, which would re-group athletes with nobody confirming it.

---

## Outstanding

**Race-night polling has still never been confirmed against a live race.** The
first window was Tuesday 25 Aug, 19:00–23:00. Check `docker logs tri-app-1` for
whether it picked the results up, and whether it stopped once it had them.

**35 rows resolve as `contested`** — two people sharing a name inside one
event. The merge tool cannot help; these need somebody who knows the club.

**Nothing verifies the route assumption against GPS.** The two time-trial
routes are matched by advertised distance alone.

**Local `data/identity.json` is a copy of the live registry** pulled 2026-08-25,
and the server has moved on by one exclusion since. Do not push local data up
without pulling first. Backups are Hetzner's, by decision — whole-VM snapshots,
so recovering one file means rolling the box back.

**Session expiry mid-sweep** is handled by stopping early rather than
re-logging-in. If sweeps start reporting "stopped early" regularly, that is the
signal to add a retry.

---

## Recently done, for context

- **Identity** — merge suggestions (`merge.py`) on their own admin host; 129
  suggestions confirmed, 89 duplicate identities collapsed, 17 → 136 athletes.
  "Not mine" now sticks, and is offered on inferred rows too.
- **Chart** — axis broken per season, all-time default, `km/h` no longer
  overprinting the top tick value.
- **Correctness** — per-leg aquathon plausibility bounds (caught 5 results whose
  totals looked ordinary but whose splits could not both be real); the sweep no
  longer caches a transient failure as "never published", which had been hiding
  new races for up to 30 days.
- **Page** — conditions note on the latest race, standings cut to 20, excluded
  events and "How this is measured" removed, aquathon draft warning replaced
  with a turnout caveat. 732 KB → ~600 KB.
