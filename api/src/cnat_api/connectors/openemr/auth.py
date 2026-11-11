"""SMART Backend Services auth for OpenEMR: client_credentials + private_key_jwt.

No user password is ever used. The tool signs a short-lived JWT with its own
private key; OpenEMR verifies it against the JWKS registered for the client.
"""
from __future__ import annotations

import base64
import threading
import time
import uuid
from dataclasses import dataclass

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from cnat_api.config import OpenEMRSettings

ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"


def _b64url_uint(n: int) -> str:
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def generate_keypair() -> tuple[bytes, dict, str]:
    """Return (private PEM, public JWK, kid)."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    nums = key.public_key().public_numbers()
    kid = uuid.uuid4().hex
    jwk = {
        "kty": "RSA",
        "kid": kid,
        "use": "sig",
        "alg": "RS384",
        "n": _b64url_uint(nums.n),
        "e": _b64url_uint(nums.e),
    }
    return pem, jwk, kid


@dataclass
class _Token:
    value: str
    expires_at: float


class OpenEMRAuth:
    def __init__(self, settings: OpenEMRSettings, http: httpx.Client | None = None):
        settings.assert_transport_ok()
        settings.assert_read_only_scopes()
        if not settings.client_id:
            raise RuntimeError("OPENEMR_CLIENT_ID is empty. Run scripts/openemr_register.py first.")
        self.s = settings
        self.http = http or httpx.Client(timeout=settings.timeout_seconds)
        self._token: _Token | None = None
        self._lock = threading.Lock()
        self._private_key = settings.private_key_file.read_bytes()

    def _client_assertion(self) -> str:
        now = int(time.time())
        claims = {
            "iss": self.s.client_id,
            "sub": self.s.client_id,
            "aud": self.s.token_url,
            "jti": uuid.uuid4().hex,
            "iat": now,
            "exp": now + 240,
        }
        headers = {"kid": self.s.key_id} if self.s.key_id else None
        return jwt.encode(claims, self._private_key, algorithm="RS384", headers=headers)

    def _fetch(self) -> _Token:
        resp = self.http.post(
            self.s.token_url,
            data={
                "grant_type": "client_credentials",
                "client_assertion_type": ASSERTION_TYPE,
                "client_assertion": self._client_assertion(),
                "scope": self.s.scopes,
            },
            headers={"Accept": "application/json"},
        )
        if resp.status_code != 200:
            raise RuntimeError(f"OpenEMR token request failed ({resp.status_code}): {resp.text[:500]}")
        body = resp.json()
        return _Token(body["access_token"], time.time() + int(body.get("expires_in", 300)))

    def token(self) -> str:
        with self._lock:
            if self._token is None or self._token.expires_at - 30 < time.time():
                self._token = self._fetch()
            return self._token.value

    def invalidate(self) -> None:
        with self._lock:
            self._token = None
