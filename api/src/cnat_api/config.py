"""Settings loaded from environment / .env (secrets never live in code)."""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]   # api/ (holds .env, keys/)
REPO_ROOT = PROJECT_ROOT.parent


def _first_existing(env: str, *candidates: Path) -> Path:
    import os
    if os.environ.get(env):
        return Path(os.environ[env])
    return next((c for c in candidates if c.exists()), candidates[0])


def ruleset_dir() -> Path:
    """Folder with the YAML rule sets: CNAT_RULESET_DIR, else the repo's rulesets/, else ./rulesets
    (the Azure package puts it next to the app)."""
    return _first_existing("CNAT_RULESET_DIR", REPO_ROOT / "rulesets", Path.cwd() / "rulesets")


def web_dist_dir() -> Path | None:
    """Built web app to serve from the API (Azure: one App Service for UI + API). None if not built."""
    d = _first_existing("CNAT_WEB_DIST", Path.cwd() / "web", REPO_ROOT / "webapp" / "dist")
    return d if (d / "index.html").exists() else None


class OpenEMRSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="OPENEMR_",
        env_file=PROJECT_ROOT / ".env",
        extra="ignore",
    )

    base_url: str = ""
    site: str = "default"
    client_id: str = ""
    client_secret: str = ""
    private_key_path: str = "keys/openemr_private.pem"
    # Azure: the PEM text itself (App Service setting / Key Vault reference). Used instead of the file.
    private_key: str = ""
    key_id: str = ""
    scopes: str = "api:oemr system/patient.read system/encounter.read system/soap_note.read"
    allow_insecure_http: bool = False
    timeout_seconds: float = 20.0

    # Intake (draft-note polling)
    sync_minutes: float = 0          # 0 = off; e.g. 5 = poll every 5 minutes
    lookback_days: int = 14          # only encounters this recent
    default_program: str | None = None

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.client_id)

    @property
    def root(self) -> str:
        return self.base_url.rstrip("/")

    @property
    def token_url(self) -> str:
        return f"{self.root}/oauth2/{self.site}/token"

    @property
    def registration_url(self) -> str:
        return f"{self.root}/oauth2/{self.site}/registration"

    @property
    def api_url(self) -> str:
        return f"{self.root}/apis/{self.site}/api"

    @property
    def fhir_url(self) -> str:
        return f"{self.root}/apis/{self.site}/fhir"

    @property
    def private_key_file(self) -> Path:
        p = Path(self.private_key_path)
        return p if p.is_absolute() else PROJECT_ROOT / p

    def private_key_bytes(self) -> bytes:
        """The signing key: OPENEMR_PRIVATE_KEY if set (literal "\\n" allowed), else the key file."""
        if self.private_key.strip():
            return self.private_key.strip().replace("\\n", "\n").encode() + b"\n"
        f = self.private_key_file
        if not f.exists():
            raise RuntimeError(
                f"OpenEMR private key not found: set OPENEMR_PRIVATE_KEY or create {f} "
                "(scripts/openemr_register.py)."
            )
        return f.read_bytes()

    def assert_transport_ok(self) -> None:
        if self.root.startswith("http://") and not self.allow_insecure_http:
            raise RuntimeError(
                "OpenEMR base URL is plain HTTP. Enable HTTPS on the server, or set "
                "OPENEMR_ALLOW_INSECURE_HTTP=1 for synthetic-data dev only."
            )

    def assert_read_only_scopes(self) -> None:
        bad = [s for s in self.scopes.split() if s.endswith(".write") or s.endswith(".*")]
        if bad:
            raise RuntimeError(f"Write scopes are not allowed (spec A1/B2.8): {bad}")
