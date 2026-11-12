"""Check the OAuth client works and see what OpenEMR exposes over FHIR.

    python api/scripts/openemr_probe.py

Prints counts only, never note text or patient identifiers.
"""
from __future__ import annotations

from datetime import date, timedelta

from cnat_api.config import OpenEMRSettings
from cnat_api.connectors.openemr.client import OpenEMRClient


def step(name, fn):
    try:
        print(f"[ok]   {name}: {fn()}")
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[fail] {name}: {e}")
        return False


def main() -> int:
    s = OpenEMRSettings()
    c = OpenEMRClient(s)
    meta = c.fhir_metadata()
    print(f"OpenEMR {meta['software']['version']}, FHIR {meta['fhirVersion']}, intake API: {s.api}")
    if not step("token (client_credentials + private_key_jwt)", lambda: "issued" if c.auth.token() else "none"):
        return 1
    since = (date.today() - timedelta(days=s.lookback_days)).isoformat()
    ok = all([
        step("Patient", lambda: f"{len(c.fhir_search('Patient', max_pages=1))} on first page"),
        step("Encounter", lambda: f"{len(c.fhir_search('Encounter', max_pages=1))} on first page"),
        step(f"Clinical notes since {since}",
             lambda: f"{len(c.fhir_search('DocumentReference', category='clinical-note', date=f'ge{since}'))}"),
    ])
    if not ok:
        print("\nIf these fail with 401: in OpenEMR, turn on 'Enable OpenEMR FHIR System Scopes' (Connectors) and Save,"
              " and check the API client is enabled.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
