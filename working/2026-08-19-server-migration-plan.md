# CTC_bot → Hetzner server, in Docker

Plan only — nothing implemented yet.

Reviewed against `C:\GitHub\Applets\infra` (`ARCHITECTURE.md`, `apps/_template`,
`apps/whiskey`, `.github/workflows/build-and-deploy.yml`).

---

## 1. Which infra pattern this app takes

The infra repo offers two shapes:

| Pattern | Used by | Fit for CTC_bot |
|---|---|---|
| **GHCR image built in CI**, SHA-pinned into `apps/<name>/compose.yml` (`apps/_template`) | the documented default (§10) | **Yes** |
| **Built on-box** from a second clone (`apps/whiskey`) | whiskey-poker | No — only needed when the app has no CI |

CTC_bot already has a GitHub remote (`JamesCarron/CTC_bot`, 28 commits pushed),
so the documented path works as-is: push to the default branch → Actions builds
and pushes `ghcr.io/JamesCarron/ctc-bot:<sha>` → the same run commits that SHA
into the infra repo → Arcane's git-watch redeploys.

§10 prefers this precisely because building on the box competes for the RAM the
apps need, and the running version stays answerable from `git log`.

> **Branch mismatch:** the reusable workflow gates on `refs/heads/main`, and
> CTC_bot's branch is **`master`**. Either rename the branch or parameterise the
> workflow. Renaming is cleaner and matches the rest of the estate.

---

## 2. What has to change in the app first

Five things block a straight lift. Two are mechanical, three are real design
work.

### 2a. Credentials — DPAPI does not exist on Linux ⚠️

`ctc_bot/credentials.py` is Windows-DPAPI-only **by design**: it refuses to
store anything if no secure backend is available, rather than falling back to
plaintext. That refusal is correct and should stay — but on the server there is
no DPAPI, so today the container simply cannot log in.

The infra repo already has the answer (§6): SOPS-encrypted `*.enc.env` in git →
decrypted at bootstrap to `0600` files under `/etc/infra/secrets` → read by the
container through compose's `env_file:`.

**Change:** add a third backend to `credentials.py` — read
`CTC_RACECLOCKER_EMAIL` / `CTC_RACECLOCKER_PASSWORD` from the environment when
present, ahead of the DPAPI file. That keeps one code path for both machines and
introduces no plaintext-on-disk fallback: the secret is only ever in the process
environment, exactly as every other app on this box does it.

`apps/ctc-bot/ctc-bot.enc.env` holds the two values, SOPS-encrypted.

### 2b. The write API has no authentication ⚠️⚠️ — the important one

This is the finding that most changes the shape of the job.

Every mutating endpoint is unauthenticated:

```
POST /api/claim   /api/adopt   /api/disown
POST /api/edit-time  /api/reset-time  /api/add-result  /api/remove-result
```

That is fine today because the server binds to `127.0.0.1` and its own docstring
says it must stay that way. Put it behind Traefik on a public hostname and
**anyone on the internet can reassign identities, rewrite times, and invent
races** — silently, because every one of those actions is designed to look
legitimate afterwards.

None of the standard middleware chain helps: `warp-auto`, `secure-headers`,
`ratelimit` and `compress` provide no authentication. Rate limiting slows an
abuser down; it does not stop them.

Four options, in order of my preference:

1. **Public read, authenticated write.** Two Traefik routers on the same
   service: an open one for `GET`, and one matching the API paths with a
   `basicauth` middleware, so the club shares one password to make changes.
   Keeps self-service claiming — the whole point of the claim form — while
   making edits accountable to whoever holds the password.
2. **Cloudflare Access** in front of the whole site — per-member email OTP, no
   shared password, free tier covers this. Strongest, but every reader needs to
   authenticate.
3. **Whole site behind basicauth.** Simplest. One password to read and write.
4. **Tailnet-only**, like Arcane. Safest, but only you can reach it, which
   defeats the claim form entirely.

**This needs your decision before anything is deployed.** My recommendation is
(1), with (2) if you would rather nobody shares a password.

Whichever is chosen, the app should also stop trusting its own binding: add a
`CTC_READ_ONLY=1` mode so a misconfigured deploy fails closed rather than open.

### 2c. State — this app breaks the "stateless box" assumption ⚠️

§16 says the box is *"effectively stateless… a total server loss costs
re-provisioning time, not data"*, and explicitly flags **"revisit if an app gains
a real database."** CTC_bot is that moment.

| Path | Size | Replaceable? |
|---|---|---|
| `data/identity.json` | small | **No.** 10 athletes, **221 claims** — hours of human judgement |
| `data/overrides.json` | small | **No.** Hand-corrected times and races the timer missed |
| `data/courses.json` | tiny | Yes (defaults in code), but holds admin edits |
| `data/raw/` | 33 MB | Yes — refetchable, but 209 authenticated requests |
| `data/events/` | 4.2 MB | Yes — regenerable from `data/raw/` offline |

So: one named volume at `/app/data`, plus a backup that actually covers the two
irreplaceable files. Hetzner's daily snapshots cover the disk, but these are
small enough that a nightly `identity.json`/`overrides.json` copy into the
Obsidian vault repo (already synced off-box by `vault-sync.sh`) is cheap
insurance and version-controlled.

**Migration:** copy `identity.json` and `overrides.json` into the volume by hand
once. Let the container backfill `raw/` and `events/` itself on first run — the
code already does this and it takes minutes.

### 2d. Nothing schedules the refresh yet

The Wed/Fri refresh was decided but never built. In a container the simplest
robust form is a scheduler thread inside the existing process: on Wed and Fri
mornings run `backfill` then rebuild the dashboard. One container, one process,
no extra moving parts, and Arcane's healthcheck already watches it.

Needs `TZ=Europe/Dublin` in the env file, or it will fire on UTC and drift an
hour across the year.

### 2e. Packaging — pixi does not travel

The local pixi environment is **1.6 GB**. It is the right tool on your Windows
box and the wrong one in an image.

Use `python:3.12-slim` + `pip install -r requirements.txt`. The requirements
file is already portable — `pywin32` is correctly guarded by
`sys_platform == "win32"`, so it simply will not install on Linux.

Two trims worth making:

- **Drop `matplotlib`** (~100 MB installed). It is listed for a PNG renderer
  that was never built. Add it back when that lands.
- Keep `truststore`. It exists for your local TLS-inspecting proxy, is a no-op
  without one, and works fine on Linux.

Expected image: roughly 150–200 MB.

---

## 3. Files to add

### In `CTC_bot/`

```
Dockerfile                       # python:3.12-slim, non-root, EXPOSE 8777
.dockerignore                    # .pixi/, data/, out/, working/, tests/
.github/workflows/deploy.yml     # calls the infra reusable workflow
```

The workflow is four lines, per the template's usage note:

```yaml
jobs:
  deploy:
    uses: JamesCarron/infra/.github/workflows/build-and-deploy.yml@main
    with:
      app_name: ctc-bot
    secrets:
      infra_repo_token: ${{ secrets.INFRA_REPO_TOKEN }}
```

### In `infra/`

```
apps/ctc-bot/compose.yml         # from apps/_template, plus a volume
apps/ctc-bot/ctc-bot.env         # PORT, TZ, CTC_READ_ONLY
apps/ctc-bot/ctc-bot.enc.env     # SOPS: RaceClocker email + password
```

Compose differs from the template in three ways: a named volume for `/app/data`,
`mem_limit: 256m` rather than 128m (peak measured allocation is 22 MB, but the
container also holds 209 parsed events — 256m is comfortable, 128m is not), and
the second authenticated router from §2b.

---

## 4. Order of work

1. **Decide the auth model** (§2b) — everything else is independent of it, but
   nothing should be published without it.
2. Add the environment credential backend + `CTC_READ_ONLY`; test on Windows.
3. Add the scheduler thread.
4. Dockerfile + `.dockerignore`; build and run locally against a copy of the
   data to prove it works before any server involvement.
5. Rename `master` → `main`; add the deploy workflow; create `INFRA_REPO_TOKEN`.
6. Create `apps/ctc-bot/` in the infra repo; SOPS-encrypt the credentials.
7. Push CTC_bot → CI builds → Arcane deploys.
8. Copy `identity.json` and `overrides.json` into the volume; let the rest
   backfill.
9. Add the nightly backup of the two irreplaceable files.
10. Point Uptime Kuma at the public URL.

Steps 2–4 are all local and reversible. The first irreversible step is 7.

---

## 5. Open questions

1. **Auth model** — §2b. Blocking.
2. **Hostname** — `ctc.jamescarron.cloud`? `ctc-bot.`? The template derives the
   router name and host from the app name.
3. **Is `JamesCarron/CTC_bot` public or private?** If private, the GHCR image
   inherits that and the box needs the GHCR pull token (secret #6, currently
   listed as optional and not yet needed by anything).
4. **Does the club get told about it?** A public URL with a claim form invites
   use; that is the point, but it changes the auth answer.
5. **Keep the Windows install working too?** The environment-variable backend
   makes both work from one codebase, so the answer only affects whether the
   local pixi setup keeps being maintained.

---

## 6. Risks

- **Publishing the write API before auth is in place.** The one genuinely
  dangerous step. Sequence guarantees it cannot happen: `CTC_READ_ONLY=1`
  defaults on, and is only turned off once the authenticated router is live.
- **Losing `identity.json`.** 221 claims, no way to regenerate. It is gitignored
  by design and must be copied by hand at migration and backed up nightly after.
- **The dashboard rebuilds on every request.** Fine on localhost, wasteful when
  public — it re-reads 209 event files per page load. Should cache and rebuild
  only when the data or the overrides change.
- **RaceClocker session handling in a long-lived process.** The local runs are
  short-lived; a container running for weeks will hit session expiry. `session.py`
  already detects being bounced to the login form, so it needs a re-login retry
  rather than new detection.
