# CTC_bot → Hetzner server, in Docker

Plan only — nothing implemented yet.

Reviewed against `C:\GitHub\Applets\infra` (`ARCHITECTURE.md`, `apps/_template`,
`apps/whiskey`, `.github/workflows/build-and-deploy.yml`).

## Decisions taken

| | |
|---|---|
| **App name** | `tri` — becomes the GHCR image, Traefik router and `apps/tri/` |
| **Hostname** | `tri.jamescarron.cloud` (wildcard DNS already in place — no DNS work) |
| **Access** | One password for the whole site: `basicauth` on a single router, read and write alike |
| **Backup** | Hetzner daily snapshots only — no per-file backup |
| **Local copy** | The server becomes the only home; the Windows data is retired after migration |
| **Build** | GHCR image built in CI, SHA-pinned into the infra repo (`apps/_template` pattern) |
| **Repo visibility** | `JamesCarron/CTC_bot` is **private** → the image is private → the box needs the GHCR pull token |

---

## 1. Which infra pattern this app takes

The infra repo offers two shapes:

| Pattern | Used by | Fit |
|---|---|---|
| **GHCR image built in CI**, SHA-pinned into `apps/<name>/compose.yml` (`apps/_template`) | the documented default (§10) | **Yes** |
| **Built on-box** from a second clone (`apps/whiskey`) | whiskey-poker | No — only needed when the app has no CI |

CTC_bot already has a GitHub remote (`JamesCarron/CTC_bot`, 28 commits pushed),
so the documented path works as-is: push → Actions builds and pushes
`ghcr.io/JamesCarron/tri:<sha>` → the same run commits that SHA into the infra
repo → Arcane's git-watch redeploys.

§10 prefers this because building on the box competes for the RAM the apps need,
and the running version stays answerable from `git log`.

Two details that will bite otherwise:

- **Branch:** the reusable workflow gates on `refs/heads/main`; CTC_bot is on
  **`master`**. Rename the branch.
- **Private image:** both repos are private, so the GHCR package will be too.
  That activates **secret #6** (GHCR pull token), which §6 lists as optional and
  nothing currently uses. It has to exist before the first deploy or the pull
  fails with a bare 401.

---

## 2. What has to change in the app first

### 2a. Credentials — DPAPI does not exist on Linux

`ctc_bot/credentials.py` is Windows-DPAPI-only **by design**: it refuses to store
anything if no secure backend is available, rather than falling back to
plaintext. That refusal is correct and stays.

The infra repo already has the answer (§6): SOPS-encrypted `*.enc.env` in git →
decrypted at bootstrap to `0600` files under `/etc/infra/secrets` → read by the
container through compose's `env_file:`.

**Change:** add an environment backend to `credentials.py` —
`CTC_RACECLOCKER_EMAIL` / `CTC_RACECLOCKER_PASSWORD`, checked ahead of the DPAPI
file. One code path for both machines, and no plaintext-on-disk fallback is
introduced: the secret only ever lives in the process environment, exactly as
every other app on this box does it.

### 2b. Access control — decided: one password for the whole site

Every mutating endpoint is currently unauthenticated:

```
POST /api/claim  /api/adopt  /api/disown
POST /api/edit-time  /api/reset-time  /api/add-result  /api/remove-result
```

Correct behind `127.0.0.1`; unacceptable behind Traefik, where anyone could
reassign identities, rewrite times and invent races — silently, since every one
of those actions is designed to look legitimate afterwards. None of the standard
chain (`warp-auto`, `secure-headers`, `ratelimit`, `compress`) authenticates
anything.

**Decision: `basicauth` on the single router**, covering read and write.
Middleware chain becomes:

```
warp-auto@file, tri-auth@file, secure-headers@file, ratelimit@file, compress@file
```

`warp-auto` stays first (§7 — the rate limiter reads the header it rewrites).
`tri-auth` is a new entry in `core/traefik/dynamic/middlewares.yml`; the htpasswd
hash goes in the SOPS-encrypted env file, not in the dynamic config, since that
file is committed in clear.

Accepted tradeoff, recorded so it is not rediscovered later: a shared password
that the whole club knows is weak protection for the edit endpoints
specifically — it authenticates "someone in the club" and nothing finer, so an
edit cannot be attributed to a person. Acceptable while the club is small and
every change is reversible (§2 of the app's own design: nothing is deleted,
everything resets). Revisit if the club grows or an edit war ever happens.

**Still worth building:** a `CTC_READ_ONLY=1` flag that refuses all mutating
endpoints. With whole-site auth it is not the primary defence, but a
mis-attached middleware is the realistic failure mode, and this makes that fail
closed rather than open. Default it on; turn it off once the auth is confirmed
working.

### 2c. State — decided: Hetzner snapshots only

§16 says the box is *"effectively stateless… a total server loss costs
re-provisioning time, not data"*, and flags **"revisit if an app gains a real
database."** This app is that moment, and the decision is to accept the existing
cover rather than add machinery.

| Path | Size | Replaceable? |
|---|---|---|
| `data/identity.json` | small | **No.** 10 athletes, **221 claims** — hours of human judgement |
| `data/overrides.json` | small | **No.** Hand-corrected times, races the timer missed |
| `data/courses.json` | tiny | Defaults are in code, but it holds admin edits |
| `data/raw/` | 33 MB | Yes — refetchable, 209 authenticated requests |
| `data/events/` | 4.2 MB | Yes — regenerable from `data/raw/` offline |

One named volume at `/app/data`. Hetzner's daily snapshots are the backup.

**Exposure this accepts:** up to 24 hours of lost claims, and no per-file
history — a bad edit noticed three days later cannot be rolled back without
restoring the whole disk to a point that also loses three days of everything
else.

**One cheap mitigation that costs nothing and fits the decision:** since the
Windows copy is being retired anyway (§2e), *archive* it rather than delete it.
A copy of `identity.json` and `overrides.json` as they stood at migration is a
free floor under the whole thing, and it already exists.

### 2d. Nothing schedules the refresh yet

Decided previously but never built: refresh on Wednesday and Friday mornings,
after the Tuesday time trial and Thursday aquathon.

Simplest robust form in a container is a scheduler thread inside the existing
process — backfill, then rebuild. One container, one process, no extra moving
parts, and Arcane's healthcheck already watches it.

Needs `TZ=Europe/Dublin` in the env file, or it fires on UTC and drifts an hour
across the year.

### 2e. Packaging — pixi does not travel

The local pixi environment is **1.6 GB**: right on your Windows box, wrong in an
image.

`python:3.12-slim` + `pip install -r requirements.txt`. The requirements file is
already portable — `pywin32` is guarded by `sys_platform == "win32"` and simply
will not install on Linux.

- **Drop `matplotlib`** (~100 MB installed) — listed for a PNG renderer that was
  never built. Add it back when that lands.
- **Keep `truststore`** — it exists for your local TLS-inspecting proxy, is a
  no-op without one, and works on Linux.

Expected image: roughly 150–200 MB.

---

## 3. Files to add

### In `CTC_bot/`

```
Dockerfile                       # python:3.12-slim, non-root, EXPOSE 8777
.dockerignore                    # .pixi/, data/, out/, working/, tests/
.github/workflows/deploy.yml     # calls the infra reusable workflow
```

```yaml
jobs:
  deploy:
    uses: JamesCarron/infra/.github/workflows/build-and-deploy.yml@main
    with:
      app_name: tri
    secrets:
      infra_repo_token: ${{ secrets.INFRA_REPO_TOKEN }}
```

### In `infra/`

```
apps/tri/compose.yml             # from apps/_template, plus a volume
apps/tri/tri.env                 # PORT, TZ, CTC_READ_ONLY
apps/tri/tri.enc.env             # SOPS: RaceClocker login + htpasswd hash
core/traefik/dynamic/middlewares.yml   # add the tri-auth basicauth middleware
```

Compose differs from the template in three ways: a named volume for `/app/data`;
`mem_limit: 256m` rather than 128m (peak measured allocation is 22 MB, but the
container also holds 209 parsed events — 256m is comfortable, 128m is not); and
`tri-auth@file` in the middleware chain.

---

## 4. Order of work

1. Environment credential backend + `CTC_READ_ONLY`, tested on Windows.
2. Scheduler thread + `TZ`.
3. Dockerfile + `.dockerignore`; build and run locally against a **copy** of the
   data, to prove the image works before the server is involved.
4. Cache the dashboard build (see Risks) — it currently re-reads 209 event files
   per request, which is fine on localhost and wasteful in public.
5. Rename `master` → `main`; add the deploy workflow; create `INFRA_REPO_TOKEN`.
6. Create the GHCR pull token (secret #6) and SOPS-encrypt it — **before** the
   first deploy, or the pull fails with a bare 401.
7. Create `apps/tri/` and the `tri-auth` middleware in the infra repo.
8. Push → CI builds → Arcane deploys, still `CTC_READ_ONLY=1`.
9. Confirm the password prompt actually appears, then turn read-only off.
10. Copy `identity.json` and `overrides.json` into the volume; let `raw/` and
    `events/` backfill themselves.
11. Archive the Windows copies; retire the local data.
12. Point Uptime Kuma at `https://tri.jamescarron.cloud`.

Steps 1–4 are local and reversible. The first irreversible step is 8.

---

## 5. Risks

- **Publishing before the password works.** Sequence guards it: `CTC_READ_ONLY=1`
  ships on, and only comes off at step 9 once the prompt is confirmed.
- **Losing `identity.json`.** 221 claims, unregenerable, and the chosen backup is
  disk-level with 24-hour granularity. Step 11's archive is the floor.
- **The dashboard rebuilds on every request** — 209 event files per page load.
  Needs caching keyed on the data and overrides before it faces real traffic.
- **Session expiry in a long-lived process.** Local runs are short; a container
  running for weeks will hit it. `session.py` already detects being bounced to
  the login form, so this needs a re-login retry, not new detection.
- **First deploy pulls a private image.** Easy to forget until it fails, and the
  error is unhelpful.
