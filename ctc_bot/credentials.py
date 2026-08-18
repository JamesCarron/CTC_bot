"""Secure local storage for the RaceClocker admin login.

The secret is encrypted with **Windows DPAPI** (`CryptProtectData`), which ties
the ciphertext to the current Windows user account. Copying the file to another
machine, or opening it as a different user, yields nothing useful - there is no
master password to remember and no key sitting next to the data.

Two deliberate choices:

* **Stored outside the repository**, under ``%LOCALAPPDATA%\\CTC_bot`` by
  default, so a secret can never be committed by accident.
* **No plaintext fallback.** If no secure backend is available the store refuses
  to write rather than quietly downgrading to a readable file.

Nothing here ever prints, logs or returns the password in an error message.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

SERVICE = "CTC_bot/raceclocker"

# Application-specific entropy, mixed into DPAPI so that another process running
# as the same user cannot unprotect this blob without knowing it. Modest
# defence, but standard practice and free.
_ENTROPY = b"CTC_bot::raceclocker::v1"


def default_path() -> Path:
    """Location of the credential file, outside the repo by design."""
    override = os.environ.get("CTC_BOT_CREDENTIALS")
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/.config")
    return Path(base) / "CTC_bot" / "credentials.bin"


class CredentialError(RuntimeError):
    """Raised when credentials cannot be stored or read."""


@dataclass
class Credentials:
    username: str
    password: str

    def __repr__(self) -> str:  # never leak the secret into logs or tracebacks
        return f"Credentials(username={self.username!r}, password=<hidden>)"


# ---- DPAPI backend -------------------------------------------------------


def _dpapi():
    try:
        import win32crypt  # type: ignore
    except ImportError:
        return None
    return win32crypt


def backend_name() -> str:
    """Which secure backend is in use, for display."""
    if _dpapi():
        return "Windows DPAPI (bound to your Windows user account)"
    return "none"


def available() -> bool:
    return _dpapi() is not None


def _encrypt(payload: bytes) -> bytes:
    win32crypt = _dpapi()
    if win32crypt is None:
        raise CredentialError(
            "No secure credential backend is available.\n"
            "Install pywin32 to enable Windows DPAPI encryption:\n"
            "    python -m pip install pywin32\n"
            "Refusing to store credentials in plaintext."
        )
    return win32crypt.CryptProtectData(payload, SERVICE, _ENTROPY, None, None, 0)


def _decrypt(blob: bytes) -> bytes:
    win32crypt = _dpapi()
    if win32crypt is None:
        raise CredentialError("pywin32 is not installed, so the stored secret cannot be read.")
    try:
        return win32crypt.CryptUnprotectData(blob, _ENTROPY, None, None, 0)[1]
    except Exception as exc:  # pragma: no cover - depends on OS state
        raise CredentialError(
            "Could not decrypt the credential file. Either it was encrypted for "
            "a different Windows user account or machine, or it has been "
            "corrupted. Re-run:  ctc login"
        ) from exc


# ---- restrictive file permissions ---------------------------------------


def _restrict_permissions(path: Path) -> None:
    """Defence in depth: make the file readable only by the current user.

    DPAPI already protects the contents; this stops other accounts on the
    machine from even reading the ciphertext. Best-effort - a failure here is
    not fatal, because the encryption is what actually protects the secret.
    """
    if os.name == "nt":
        user = os.environ.get("USERNAME")
        if not user:
            return
        try:
            subprocess.run(
                ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:F"],
                check=False,
                capture_output=True,
                timeout=15,
            )
        except (OSError, subprocess.SubprocessError):
            pass
    else:
        try:
            path.chmod(0o600)
        except OSError:
            pass


# ---- public API ----------------------------------------------------------


def store(username: str, password: str, path: Path | None = None) -> Path:
    """Encrypt and write the credentials. Returns the file path."""
    target = path or default_path()
    payload = json.dumps({"username": username, "password": password}).encode("utf-8")
    blob = _encrypt(payload)

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(blob)
    _restrict_permissions(target)
    return target


def load(path: Path | None = None) -> Credentials:
    """Read and decrypt the stored credentials."""
    target = path or default_path()
    if not target.exists():
        raise CredentialError(
            f"No credentials stored at {target}.\n"
            "Run:  ctc login"
        )
    data = json.loads(_decrypt(target.read_bytes()).decode("utf-8"))
    return Credentials(username=data["username"], password=data["password"])


def exists(path: Path | None = None) -> bool:
    return (path or default_path()).exists()


def delete(path: Path | None = None) -> bool:
    """Remove the stored credentials. Returns True if a file was removed."""
    target = path or default_path()
    if target.exists():
        target.unlink()
        return True
    return False
