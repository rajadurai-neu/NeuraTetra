"""Settings loaded from environment / .env (secrets never live in code)."""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]   # api/ (holds .env, keys/)
REPO_ROOT = PROJECT_ROOT.parent


def ruleset_dir() -> Path:
    """Folder with the YAML rule sets. CNAT_RULESET_DIR overrides the repo default."""
    import os
    return Path(os.environ.get("CNAT_RULESET_DIR") or REPO_ROOT / "rulesets")


class OpenEMRSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="OPENEMR_",
        env_file=PROJECT_ROOT / ".env",
        extra="ignore",
    )

    base_url: str
    site: str = "default"
    client_id: str = ""
    client_secret: str = ""
    private_key_path: str = "keys/openemr_private.pem"
    key_id: str = ""
    scopes: str = "api:oemr system/patient.read system/encounter.read system/soap_note.read"
    allow_insecure_http: bool = False
    timeout_seconds: float = 20.0

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
