"""Check the OAuth client works and see what note data OpenEMR exposes.

    backend/.venv/bin/python scripts/openemr_probe.py

Prints counts and field names only, never note text or patient identifiers.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from cnat.config import OpenEMRSettings  # noqa: E402
from cnat.connectors.openemr.client import OpenEMRClient, OpenEMRError  # noqa: E402


def step(name, fn):
    try:
        out = fn()
        print(f"[ok]   {name}: {out}")
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[fail] {name}: {e}")
        return False


def main() -> int:
    s = OpenEMRSettings()
    c = OpenEMRClient(s)

    meta = c.fhir_metadata()
    print(f"OpenEMR {meta['software']['version']}, FHIR {meta['fhirVersion']}")

    if not step("token (client_credentials + private_key_jwt)", lambda: "issued" if c.auth.token() else "none"):
        return 1

    patients: list[dict] = []

    def _patients():
        patients.extend(c.patients())
        return f"{len(patients)} patients; fields={sorted(patients[0].keys())[:15] if patients else []}"

    step("GET /api/patient", _patients)
    step("GET /api/practitioner", lambda: f"{len(c.practitioners())} practitioners")

    notes_found = 0
    for p in patients[:10]:
        try:
            encs = c.encounters(p["uuid"])
        except OpenEMRError as e:
            print(f"[fail] encounters: {e}")
            break
        for e in encs[:10]:
            pid, eid = p.get("pid"), e.get("eid") or e.get("encounter")
            try:
                notes = c.soap_notes(pid, eid)
            except OpenEMRError as err:
                print(f"[fail] soap_note: {err}")
                break
            if notes and notes_found == 0:
                print(f"[ok]   SOAP note fields: {sorted(notes[0].keys())}")
            notes_found += len(notes)
    print(f"SOAP notes found across first 10 patients: {notes_found}")
    if notes_found == 0:
        print("  (No SOAP notes yet; add synthetic draft SOAP notes in OpenEMR to test.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
