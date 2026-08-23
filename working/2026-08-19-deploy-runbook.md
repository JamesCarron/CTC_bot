# Deploying tri (CTC_bot) — runbook

Everything up to the first server change is done and committed. What follows is
the part that needs you: two things cannot be done from here, because Docker and
SOPS are not installed on this machine and the age key lives only on the server.

---

## Done already

**In `CTC_bot`** (committed, `ee09d78`):

- Environment credential backend (`CTC_RACECLOCKER_EMAIL` / `_PASSWORD`), read
  ahead of the DPAPI file.
- `CTC_READ_ONLY` — refuses every mutating endpoint. Defaults **on** in the image.
- Weekly refresh thread, Wednesday and Friday at 07:00 local.
- `Dockerfile` (python:3.12-slim, non-root, healthcheck) and `.dockerignore`.
- Dashboard cache — 2574 ms → 15 ms per request.
- `.github/workflows/deploy.yml`.
- `matplotlib` dropped from both dependency files.
- 258 tests passing.

**In `infra`** (staged locally, **not pushed** — pushing is what deploys):

- `apps/tri/compose.yml`, `apps/tri/tri.env`
- `core/traefik/dynamic/middlewares.yml` — new `tri-auth` basicAuth middleware
- `core/compose.yml` — mounts the htpasswd file into Traefik
- `core/secrets/tri.enc.env.example`, `core/secrets/htpasswd.enc.env.example`

---

## Two findings about the infra repo

**1. `04-secrets.sh` only decrypts `core/secrets/*.enc.env`.** It does not touch
`apps/*/*.enc.env`, even though `apps/_template/app.env.example` tells you to put
app secrets there. Both of tri's secrets therefore live in `core/secrets/`. Worth
either fixing the script or correcting the template comment — as it stands the
template's advice silently produces a secret nothing decrypts.

**2. A public repo calling a private repo's reusable workflow needs permission.**
`CTC_bot` is public, `infra` is private. Set
**infra → Settings → Actions → General → Access → "Accessible from repositories
owned by the user"**, or the run fails at workflow resolution before any build
starts. This is separate from the server's deploy key.

---

## Your steps

### 1. GitHub settings

- `infra` → Settings → Actions → General → Access → *accessible from
  repositories owned by the user*.
- `CTC_bot` → rename branch `master` → `main` (the workflow triggers on `main`):
  ```bash
  git branch -m master main && git push -u origin main
  # then set the default branch to main in Settings → Branches, and delete master
  ```
- `CTC_bot` → Settings → Secrets → Actions → new secret **`INFRA_REPO_TOKEN`**:
  a fine-grained PAT, `contents: write`, scoped to `infra` only.

### 2. Secrets, on the server

The age key is there, so this has to happen there.

```bash
cd /opt/infra

# RaceClocker login
cp core/secrets/tri.enc.env.example core/secrets/tri.enc.env
$EDITOR core/secrets/tri.enc.env        # real email + password
sops -e -i core/secrets/tri.enc.env

# Site password (CTC2026). Generate the hash on the box rather than pasting
# one from elsewhere:
htpasswd -nbB ctc 'CTC2026'             # -> ctc:$2y$05$...
cp core/secrets/htpasswd.enc.env.example core/secrets/htpasswd.enc.env
$EDITOR core/secrets/htpasswd.enc.env   # paste the line, replacing the placeholder
sops -e -i core/secrets/htpasswd.enc.env

# Confirm both are actually encrypted before staging
head -n3 core/secrets/*.enc.env         # should show sops metadata, not values

./scripts/04-secrets.sh                 # decrypts to $SECRETS_DIR
./scripts/05-core-stack.sh              # recreates Traefik so it sees the new mount
```

> `05-core-stack.sh` force-recreates Traefik on every run anyway, which is what
> picks up both the new bind mount and the new middleware.

### 3. Push infra

```bash
cd C:/GitHub/Applets/infra
git add apps/tri core/compose.yml core/traefik/dynamic/middlewares.yml core/secrets/*.example
git commit -m "Add tri (CTC_bot) app and its basicauth middleware"
git push
```

Arcane will try to deploy `apps/tri` and fail — the image tag is still
`REPLACE_WITH_SHA`. That is expected and harmless; step 4 fixes it.

### 4. Push CTC_bot to `main`

CI builds `ghcr.io/JamesCarron/tri:<sha>`, commits the tag into `apps/tri/compose.yml`,
Arcane deploys it. The container starts **read-only**.

### 5. Confirm the password gate — before anything else

```bash
curl -sI https://tri.jamescarron.cloud | head -n1          # expect 401
curl -sI -u ctc:CTC2026 https://tri.jamescarron.cloud | head -n1   # expect 200
```

**If the first command returns 200, stop.** The middleware has not attached and
the site is open. `CTC_READ_ONLY=1` means nothing can be changed yet, which is
exactly why it ships on.

### 6. Migrate the data

```bash
# on the server, with the container stopped or not
docker cp data/identity.json  <container>:/app/data/identity.json
docker cp data/overrides.json <container>:/app/data/overrides.json
docker cp data/courses.json   <container>:/app/data/courses.json
```

Copy from this machine first — they are gitignored and exist nowhere else.
`raw/` and `events/` do **not** need copying: the first scheduled refresh
backfills them, or run it by hand.

### 7. Turn writes on

Set `CTC_READ_ONLY=0` in `apps/tri/tri.env`, commit, push. Arcane redeploys.
Confirm a claim actually saves.

### 8. Retire the local copy

Archive `data/identity.json` and `data/overrides.json` somewhere safe — with
snapshot-only backup, this archive is the floor under 221 hand-made claims.
Then stop using the Windows instance for real data.

### 9. Uptime Kuma

Add an HTTP monitor for `https://tri.jamescarron.cloud`. It will 401 without
credentials, so either set basic auth on the monitor or accept 401 as healthy.

---

## Still worth doing afterwards

- **Session expiry.** The container runs for weeks; RaceClocker sessions do not
  last that long. `session.py` already detects being bounced to the login form,
  so this is a re-login retry rather than new detection. The refresh will fail
  and log until it is added — it will not crash.
- **`mem_limit: 256m`** is a considered guess (measured peak allocation 22 MB
  plus ~200 parsed events). Watch it in Arcane for the first week.
- Nothing sets a container-side backup. Snapshots only, as decided.
