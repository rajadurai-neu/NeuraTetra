"""Simple built-in sign-in, for when Microsoft Entra ID (App Service sign-in) isn't available.

Accounts live in one setting, CNAT_USERS (JSON), with hashed passwords only:
  [{"username": "admin", "name": "Admin Person", "roles": ["admin"], "password_hash": "pbkdf2_sha256$..."}]
Make a hash with:  python api/scripts/hash_password.py
CNAT_SESSION_SECRET (long random string) signs the session cookie.

If App Service sign-in is turned on later, its identity header takes priority (see security.py).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import threading
import time
from dataclasses import dataclass

from pydantic_settings import BaseSettings, SettingsConfigDict

from cnat_api.config import PROJECT_ROOT

log = logging.getLogger("cnat.auth")

COOKIE = "cnat_session"
ITERATIONS = 310_000
MAX_FAILURES = 5
LOCKOUT_SECONDS = 300


class PasswordAuthSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CNAT_", env_file=PROJECT_ROOT / ".env", extra="ignore")
    users: str = ""
    session_secret: str = ""
    session_hours: float = 12
    cookie_secure: bool = True   # HTTPS only; set CNAT_COOKIE_SECURE=0 for plain-http local testing


@dataclass(frozen=True)
class Account:
    username: str
    name: str
    roles: tuple[str, ...]
    password_hash: str


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def hash_password(password: str, *, iterations: int = ITERATIONS) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"pbkdf2_sha256${iterations}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt, digest = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        calc = hashlib.pbkdf2_hmac("sha256", password.encode(), _unb64(salt), int(iterations))
        return hmac.compare_digest(calc, _unb64(digest))
    except (ValueError, TypeError):
        return False


class PasswordAuth:
    def __init__(self, settings: PasswordAuthSettings | None = None, clock=time.time):
        self.settings = settings or PasswordAuthSettings()
        self.clock = clock
        self.accounts: dict[str, Account] = {}
        if self.settings.users.strip():
            for u in json.loads(self.settings.users):
                acc = Account(username=str(u["username"]).strip().lower(), name=str(u.get("name") or u["username"]),
                              roles=tuple(str(r).lower() for r in u.get("roles", [])),
                              password_hash=str(u["password_hash"]))
                self.accounts[acc.username] = acc
        if self.accounts and len(self.settings.session_secret) < 32:
            raise ValueError("CNAT_SESSION_SECRET must be at least 32 characters when CNAT_USERS is set.")
        self._failures: dict[str, tuple[int, float]] = {}
        self._lock = threading.Lock()
        # Compare against a real hash even for unknown users, so timing doesn't reveal which usernames exist.
        self._dummy_hash = hash_password(os.urandom(8).hex(), iterations=ITERATIONS)

    @property
    def enabled(self) -> bool:
        return bool(self.accounts)

    # ---- sign-in ----
    def locked(self, username: str) -> bool:
        with self._lock:
            n, until = self._failures.get(username, (0, 0.0))
            return n >= MAX_FAILURES and self.clock() < until

    def authenticate(self, username: str, password: str) -> Account | None:
        username = username.strip().lower()
        if self.locked(username):
            log.warning("sign-in blocked (too many attempts): %s", username)
            return None
        acc = self.accounts.get(username)
        ok = verify_password(password, acc.password_hash if acc else self._dummy_hash) and acc is not None
        with self._lock:
            if ok:
                self._failures.pop(username, None)
            else:
                n, _ = self._failures.get(username, (0, 0.0))
                self._failures[username] = (n + 1, self.clock() + LOCKOUT_SECONDS)
        log.info("sign-in %s: %s", "ok" if ok else "failed", username)
        return acc if ok else None

    # ---- session cookie: base64(json).signature ----
    def _sign(self, payload: str) -> str:
        return _b64(hmac.new(self.settings.session_secret.encode(), payload.encode(), hashlib.sha256).digest())

    def make_session(self, acc: Account) -> str:
        exp = int(self.clock() + self.settings.session_hours * 3600)
        payload = _b64(json.dumps({"u": acc.username, "exp": exp}).encode())
        return f"{payload}.{self._sign(payload)}"

    def read_session(self, token: str | None) -> Account | None:
        """Account for a valid, unexpired cookie. Looked up fresh, so removed users lose access at once."""
        if not token or "." not in token or not self.enabled:
            return None
        payload, sig = token.rsplit(".", 1)
        if not hmac.compare_digest(sig, self._sign(payload)):
            return None
        try:
            data = json.loads(_unb64(payload))
        except ValueError:
            return None
        if data.get("exp", 0) < self.clock():
            return None
        return self.accounts.get(data.get("u", ""))
