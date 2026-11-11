"""Built-in sign-in: hashed passwords, signed session cookie, lockout, role scoping."""
import json

import pytest
from fastapi.testclient import TestClient

from cnat_api.config import ruleset_dir
from cnat_api.db.session import init_db, make_engine, make_session_factory
from cnat_api.services.assessments import AssessmentService
from cnat_api.web.app import create_app
from cnat_api.web.password_auth import COOKIE, PasswordAuth, PasswordAuthSettings, hash_password, verify_password
from cnat_api.web.security import AuthSettings
from cnat_engine.ai.reviewer import NoAIReviewer
from cnat_engine.rules.ruleset import load_ruleset

SECRET = "s" * 40
USERS = json.dumps([
    {"username": "Admin", "name": "Admin Person", "roles": ["admin"], "password_hash": hash_password("correct horse 1", iterations=1000)},
    {"username": "clin", "name": "Synthetic Clinician", "roles": ["clinician"], "password_hash": hash_password("clinician pw 12", iterations=1000)},
])


class Clock:
    t = 1_800_000_000.0

    def __call__(self):
        return self.t


@pytest.fixture
def env():
    clock = Clock()
    pw = PasswordAuth(PasswordAuthSettings(users=USERS, session_secret=SECRET, cookie_secure=False), clock=clock)
    engine = make_engine("sqlite://")
    init_db(engine)
    svc = AssessmentService(make_session_factory(engine), load_ruleset(ruleset_dir()), NoAIReviewer())
    return TestClient(create_app(svc, AuthSettings(dev_auth=False), password_auth=pw)), pw, clock


def test_hash_roundtrip():
    h = hash_password("abc", iterations=1000)
    assert verify_password("abc", h) and not verify_password("abd", h) and not verify_password("abc", "junk")


def test_login_sets_cookie_and_scopes_by_role(env):
    c, _, _ = env
    assert c.get("/api/auth/config").json() == {"mode": "password"}
    assert c.get("/api/me").status_code == 401
    r = c.post("/api/auth/login", json={"username": "admin", "password": "correct horse 1"})  # case-insensitive
    assert r.status_code == 200 and r.json()["roles"] == ["admin"]
    assert "httponly" in r.headers["set-cookie"].lower()
    assert c.get("/api/me").json() == {"name": "Admin Person", "roles": ["admin"]}
    assert c.get("/api/admin/ping").status_code == 200
    c.post("/api/auth/logout")
    c.cookies.clear()
    assert c.get("/api/me").status_code == 401


def test_clinician_cannot_use_admin_routes(env):
    c, _, _ = env
    c.post("/api/auth/login", json={"username": "clin", "password": "clinician pw 12"})
    assert c.get("/api/me").json()["name"] == "Synthetic Clinician"
    assert c.get("/api/admin/ping").status_code == 403


def test_wrong_password_and_lockout(env):
    c, _, clock = env
    for _ in range(5):
        assert c.post("/api/auth/login", json={"username": "clin", "password": "nope"}).status_code == 401
    assert c.post("/api/auth/login", json={"username": "clin", "password": "clinician pw 12"}).status_code == 429
    clock.t += 301
    assert c.post("/api/auth/login", json={"username": "clin", "password": "clinician pw 12"}).status_code == 200


def test_tampered_or_expired_cookie_rejected(env):
    c, pw, clock = env
    token = pw.make_session(pw.accounts["clin"])
    payload, sig = token.split(".")
    forged = json.dumps({"u": "admin", "exp": 9_999_999_999}).encode()
    import base64
    forged_payload = base64.urlsafe_b64encode(forged).rstrip(b"=").decode()
    assert pw.read_session(f"{forged_payload}.{sig}") is None
    assert pw.read_session(token).username == "clin"
    clock.t += 13 * 3600
    assert pw.read_session(token) is None
    c.cookies.set(COOKIE, token)
    assert c.get("/api/me").status_code == 401


def test_dev_headers_ignored_when_dev_auth_off(env):
    c, _, _ = env
    assert c.get("/api/me", headers={"X-Dev-User": "x", "X-Dev-Role": "admin"}).status_code == 401


def test_short_secret_refused():
    with pytest.raises(ValueError):
        PasswordAuth(PasswordAuthSettings(users=USERS, session_secret="short"))


def test_off_when_no_users(env):
    pw = PasswordAuth(PasswordAuthSettings(users="", session_secret=""))
    assert not pw.enabled
