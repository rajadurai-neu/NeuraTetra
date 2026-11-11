"""Who is calling, and what they may see.

In Azure App Service, turn on Authentication ("Easy Auth") with Microsoft Entra ID. App Service then
signs users in and passes their identity to the app in the X-MS-CLIENT-PRINCIPAL header, including the
app roles assigned in Entra ID (Clinician / Supervisor / QA / Admin).

Without Entra ID, the app's own sign-in page can be used instead (password_auth.py, CNAT_USERS).

Locally, set CNAT_DEV_AUTH=1 and send X-Dev-User / X-Dev-Role headers instead.
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from enum import Enum

from fastapi import Depends, HTTPException, Request
from pydantic_settings import BaseSettings, SettingsConfigDict

from cnat_api.config import PROJECT_ROOT


class Role(str, Enum):
    CLINICIAN = "clinician"
    SUPERVISOR = "supervisor"
    QA = "qa"
    ADMIN = "admin"


class AuthSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CNAT_", env_file=PROJECT_ROOT / ".env", extra="ignore")
    dev_auth: bool = False


@dataclass(frozen=True)
class User:
    name: str
    roles: frozenset[Role]

    def has(self, *roles: Role) -> bool:
        return bool(self.roles & set(roles))

    @property
    def sees_everything(self) -> bool:
        return self.has(Role.QA, Role.ADMIN)


def _roles(values) -> frozenset[Role]:
    out = set()
    for v in values:
        try:
            out.add(Role(str(v).strip().lower()))
        except ValueError:
            pass
    return frozenset(out)


def _from_easy_auth(request: Request) -> User | None:
    raw = request.headers.get("x-ms-client-principal")
    if not raw:
        return None
    data = json.loads(base64.b64decode(raw))
    claims = data.get("claims", [])
    role_type = data.get("role_typ", "roles")
    name_type = data.get("name_typ", "name")
    roles = [c["val"] for c in claims if c.get("typ") in (role_type, "roles")]
    name = next((c["val"] for c in claims if c.get("typ") == name_type), None) \
        or request.headers.get("x-ms-client-principal-name")
    return User(name=name or "unknown", roles=_roles(roles)) if name else None


def _from_session(request: Request) -> User | None:
    """Built-in sign-in (password_auth.py), used when App Service sign-in isn't available."""
    pw = getattr(request.app.state, "password_auth", None)
    if pw is None or not pw.enabled:
        return None
    from cnat_api.web.password_auth import COOKIE
    acc = pw.read_session(request.cookies.get(COOKIE))
    return User(name=acc.name, roles=_roles(acc.roles)) if acc else None


def current_user(request: Request) -> User:
    settings: AuthSettings = request.app.state.auth_settings
    user = _from_easy_auth(request)
    if user is None:
        user = _from_session(request)
    if user is None and settings.dev_auth:
        name = request.headers.get("x-dev-user")
        if name:
            user = User(name=name, roles=_roles(request.headers.get("x-dev-role", "clinician").split(",")))
    if user is None:
        raise HTTPException(401, "Sign in required.")
    if not user.roles:
        raise HTTPException(403, "No CNAT role assigned to this account.")
    return user


def require(*roles: Role):
    def dep(user: User = Depends(current_user)) -> User:
        if not user.has(*roles):
            raise HTTPException(403, "Not allowed for your role.")
        return user
    return dep


def can_see_note(user: User, clinician: str | None, supervisor: str | None) -> bool:
    """Clinicians: own notes. Supervisors: own + team's. QA/Admin: all (spec B5.23)."""
    if user.sees_everything:
        return True
    if user.has(Role.SUPERVISOR) and supervisor == user.name:
        return True
    return clinician == user.name


def scope_filters(user: User) -> dict:
    """Restrict list queries to what the user may see."""
    if user.sees_everything:
        return {}
    if user.has(Role.SUPERVISOR):
        return {"supervisor": user.name}
    return {"clinician": user.name}
