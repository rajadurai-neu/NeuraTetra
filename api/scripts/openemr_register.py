"""Register this tool as a read-only SMART Backend Services client in OpenEMR.

Run it YOURSELF (it creates credentials):
    python api/scripts/openemr_register.py

It will:
  1. generate an RSA key pair in keys/ (gitignored),
  2. POST a dynamic client registration with read-only system scopes + the public JWKS,
  3. write OPENEMR_CLIENT_ID / OPENEMR_CLIENT_SECRET / OPENEMR_KEY_ID into .env.
Secrets are never printed. An OpenEMR admin must then enable the client.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

import httpx  # noqa: E402
from dotenv import set_key  # noqa: E402

from cnat_api.config import OpenEMRSettings  # noqa: E402
from cnat_api.connectors.openemr.auth import generate_keypair  # noqa: E402


def main() -> int:
    env_file = ROOT / ".env"
    if not env_file.exists():
        env_file.write_text((ROOT / ".env.example").read_text())
        print("Created .env from .env.example")

    s = OpenEMRSettings()
    s.assert_transport_ok()
    s.assert_read_only_scopes()

    if s.client_id and "--force" not in sys.argv:
        print("OPENEMR_CLIENT_ID is already set in .env. Re-run with --force to register a new client.")
        return 1

    key_file = s.private_key_file
    key_file.parent.mkdir(parents=True, exist_ok=True)
    pem, jwk, kid = generate_keypair()

    payload = {
        "application_type": "private",
        "client_name": "Clinical Notes Assessment Tool (Dev)",
        # Required by OpenEMR's registration endpoint; unused by the backend flow.
        "redirect_uris": ["http://localhost:8000/oauth/callback"],
        "token_endpoint_auth_method": "client_secret_post",
        "scope": s.scopes,
        "jwks": {"keys": [jwk]},
    }
    resp = httpx.post(s.registration_url, json=payload, timeout=30)
    if resp.status_code not in (200, 201):
        print(f"Registration failed ({resp.status_code}): {resp.text[:800]}")
        return 2
    body = resp.json()

    key_file.write_bytes(pem)
    os.chmod(key_file, 0o600)
    set_key(str(env_file), "OPENEMR_CLIENT_ID", body["client_id"], quote_mode="never")
    set_key(str(env_file), "OPENEMR_CLIENT_SECRET", body.get("client_secret", ""), quote_mode="never")
    set_key(str(env_file), "OPENEMR_KEY_ID", kid, quote_mode="never")

    print("Registered. client_id, secret and key id saved to .env; private key at", key_file)
    print("Granted scopes requested:", s.scopes)
    print(
        "\nNext, in OpenEMR as admin:\n"
        "  - Administration > System > API Clients: open 'Clinical Notes Assessment Tool (Dev)' and Enable it.\n"
        "  - Administration > Config > Connectors: make sure these are ON:\n"
        "      * Enable OpenEMR Standard REST API\n"
        "      * Enable OpenEMR Standard REST API system scopes\n"
        "      * Enable OpenEMR FHIR REST API\n"
        "    and 'Site Address Override' = " + s.root + "\n"
        "Then run: python api/scripts/openemr_probe.py"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
