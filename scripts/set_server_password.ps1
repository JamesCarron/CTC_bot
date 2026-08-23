# =============================================================================
# Set the RaceClocker password in the server's SOPS-encrypted secret.
#
# Invoked by set-server-password.bat. See that file for why the password never
# becomes a command argument.
#
# Flow:
#   1. Prompt twice, hidden, and confirm they match.
#   2. Pipe it to the server on stdin. The remote script reads one line and
#      exports it; the value is never in argv, so it never appears in `ps`.
#   3. Decrypt, substitute the one line, re-encrypt, shred the temporaries.
#   4. Re-run 04-secrets.sh and RECREATE the container - docker reads env_file
#      only at creation, so a restart would keep serving the old value.
#   5. Verify the container received it, then that RaceClocker accepts it.
#   6. Copy the re-encrypted secret back here, commit and push, so the infra
#      repo matches the server rather than drifting from it.
# =============================================================================
[CmdletBinding()]
param(
    [string]$ServerHost = "100.68.148.7",
    [string]$User       = "admin",
    [string]$KeyFile    = "$env:USERPROFILE\.ssh\id_ed25519_hetzner",
    [string]$InfraPath  = "C:\GitHub\Applets\infra",
    [switch]$NoPush
)

$ErrorActionPreference = "Stop"

function Fail($message) { Write-Host "  $message" -ForegroundColor Red; exit 1 }
function Ok($message)   { Write-Host "  $message" -ForegroundColor Green }

Write-Host "RaceClocker password -> tri.jamescarron.cloud"
Write-Host ("-" * 60)

if (-not (Test-Path $KeyFile)) { Fail "SSH key not found: $KeyFile" }

# --- 1. prompt, hidden, twice ------------------------------------------------

$first  = Read-Host "RaceClocker password" -AsSecureString
$second = Read-Host "Confirm" -AsSecureString

function Reveal($secure) {
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
}

$plain = Reveal $first
if ($plain -ne (Reveal $second)) { Fail "Passwords did not match - nothing changed." }
if ([string]::IsNullOrWhiteSpace($plain)) { Fail "Empty password - nothing changed." }
if ($plain -match "[`r`n]") { Fail "Password contains a line break, which dotenv cannot hold." }

Write-Host ""
Write-Host "Updating the server..."

# --- 2-5. do the work on the box --------------------------------------------
# Single-quoted here-string: PowerShell must not touch $ or backticks, they are
# all for bash. The password arrives on stdin, never in this text.
$remote = @'
set -euo pipefail
umask 077

IFS= read -r PW
export PW
[ -n "$PW" ] || { echo "no password received"; exit 1; }

cd /opt/infra
enc=core/secrets/tri.enc.env
[ -f "$enc" ] || { echo "$enc missing - has the app been deployed?"; exit 1; }

plain=$(mktemp); updated=$(mktemp); backup=$(mktemp)
cp "$enc" "$backup"

# If anything below fails, put the original encrypted file back. The window
# where plaintext briefly sits at $enc (so that .sops.yaml's path-based
# creation rules match it) must never be left behind on an error.
restore() {
  if ! grep -q 'ENC\[' "$enc" 2>/dev/null; then cp "$backup" "$enc"; fi
  shred -u "$plain" "$updated" "$backup" 2>/dev/null || rm -f "$plain" "$updated" "$backup"
}
trap restore EXIT

sops -d "$enc" > "$plain"

# ENVIRON, not -v: an awk -v assignment puts the value in argv, where any user
# on the box can read it out of ps for as long as awk runs.
awk '
  /^CTC_RACECLOCKER_PASSWORD=/ { print "CTC_RACECLOCKER_PASSWORD=" ENVIRON["PW"]; seen=1; next }
  { print }
  END { if (!seen) print "CTC_RACECLOCKER_PASSWORD=" ENVIRON["PW"] }
' "$plain" > "$updated"

# Encrypted in place, not from the temp file: .sops.yaml matches creation rules
# on PATH, and a /tmp path matches nothing ("no matching creation rules found").
cp "$updated" "$enc"
sops -e -i "$enc"
grep -q 'ENC\[' "$enc" || { echo "re-encryption produced no ciphertext - refusing"; exit 1; }
echo "  secret re-encrypted"

# Confirm the value actually round-trips before touching the container - a
# silent loss here would otherwise show up as "wrong password" much later.
stored=$(sops -d "$enc" | awk -F= '/^CTC_RACECLOCKER_PASSWORD=/{print length($0)-length($1)-1}')
[ -n "$stored" ] && [ "$stored" -gt 0 ] || { echo "password did not survive encryption - refusing"; exit 1; }
echo "  stored and verified ($stored characters)"

./scripts/04-secrets.sh >/dev/null 2>&1
echo "  decrypted to \$SECRETS_DIR"

# RECREATE, not restart. Docker reads env_file once, when the container is
# created; `docker restart` reuses the environment baked in at that moment and
# would silently keep serving the old (empty) password.
cd /opt/infra
docker compose --env-file config.env -f apps/tri/compose.yml -p tri up -d --force-recreate >/dev/null 2>&1
echo "  container recreated"
sleep 15

# Prove the new value actually reached the process, not just the file.
docker exec tri-app-1 python -c "
import os
n = len(os.environ.get('CTC_RACECLOCKER_PASSWORD',''))
print(f'  container sees {n} characters')
raise SystemExit(0 if n else 3)
"

# Verify against RaceClocker from inside the container, so this reports on the
# credential the app will actually use rather than one we hope matches.
docker exec tri-app-1 python -c "
from ctc_bot import session
r = session.check()
print('  login:', 'OK' if r.ok else 'FAILED -', r.detail.splitlines()[0])
raise SystemExit(0 if r.ok else 2)
"
'@

# Pipe the password in as the first line of stdin.
$plain | & ssh -i $KeyFile -o BatchMode=yes "$User@$ServerHost" $remote
$sshCode = $LASTEXITCODE

# Drop the plaintext as soon as it is no longer needed.
Set-Variable -Name plain -Value $null
[System.GC]::Collect()

if ($sshCode -ne 0) {
    if ($sshCode -eq 2) {
        Fail "Stored, and the container received it, but RaceClocker rejected it. Check the password."
    }
    if ($sshCode -eq 3) {
        Fail "Stored, but the container did not pick it up. Check `docker logs tri-app-1`."
    }
    Fail "Server update failed (exit $sshCode). Nothing else was changed."
}
Ok "Password set and verified."

# --- 6. bring the encrypted secret back into git -----------------------------

if (-not (Test-Path $InfraPath)) {
    Write-Host "  infra repo not found at $InfraPath - skipping the git step." -ForegroundColor Yellow
    exit 0
}

Write-Host ""
Write-Host "Syncing the encrypted secret back to the infra repo..."
$dest = Join-Path $InfraPath "core\secrets\tri.enc.env"
& scp -i $KeyFile -q "${User}@${ServerHost}:/opt/infra/core/secrets/tri.enc.env" $dest
if ($LASTEXITCODE -ne 0) { Fail "Could not copy the secret back." }

# Never commit something that is not actually encrypted.
if (-not (Select-String -Path $dest -Pattern 'ENC\[' -Quiet)) {
    Remove-Item $dest -Force
    Fail "The file copied back is not encrypted. Refusing to commit it."
}
Ok "Encrypted (contains SOPS ciphertext, not values)."

if ($NoPush) {
    Write-Host "  --NoPush given; staged but not committed." -ForegroundColor Yellow
    exit 0
}

Push-Location $InfraPath
try {
    & git add core/secrets/tri.enc.env
    & git diff --cached --quiet
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  No change to commit - the repo already had this secret."
    } else {
        & git commit -q -m "Set tri's RaceClocker password

Encrypted on the server, where the age key lives, then copied back so the
repo matches what is deployed rather than drifting from it."
        & git push -q
        Ok "Committed and pushed."
    }
} finally { Pop-Location }

Write-Host ""
Ok "Done. The Wed/Fri refresh will now be able to log in."
