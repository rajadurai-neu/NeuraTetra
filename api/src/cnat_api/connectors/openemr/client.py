"""Read-only HTTP client for the OpenEMR Standard REST API and FHIR API.

Only GET is exposed: the tool never edits, signs or deletes EHR data (spec A1.5).
"""
from __future__ import annotations

import time
from typing import Any

import httpx

from cnat_api.config import OpenEMRSettings
from cnat_api.connectors.openemr.auth import OpenEMRAuth


class OpenEMRError(RuntimeError):
    pass


class OpenEMRClient:
    def __init__(self, settings: OpenEMRSettings, auth: OpenEMRAuth | None = None,
                 http: httpx.Client | None = None, max_retries: int = 3):
        self.s = settings
        self.http = http or httpx.Client(timeout=settings.timeout_seconds)
        self.auth = auth or OpenEMRAuth(settings, self.http)
        self.max_retries = max_retries

    def _get(self, url: str, params: dict | None = None) -> Any:
        last: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = self.http.get(
                    url,
                    params=params,
                    headers={"Authorization": f"Bearer {self.auth.token()}", "Accept": "application/json"},
                )
            except httpx.TransportError as e:  # network blip: retry with backoff (B2.7)
                last = e
            else:
                if resp.status_code == 401 and attempt == 0:
                    self.auth.invalidate()
                    continue
                if resp.status_code in (429, 500, 502, 503, 504):
                    last = OpenEMRError(f"{resp.status_code} from {url}")
                elif resp.status_code >= 400:
                    raise OpenEMRError(f"GET {url} -> {resp.status_code}: {resp.text[:300]}")
                else:
                    return resp.json() if resp.content else None
            time.sleep(min(2 ** attempt, 10))
        raise OpenEMRError(f"GET {url} failed after retries: {last}")

    @staticmethod
    def _data(body: Any) -> list[dict]:
        """Standard API wraps results as {data, validationErrors, internalErrors}."""
        if isinstance(body, dict) and "data" in body:
            errs = body.get("internalErrors") or body.get("validationErrors")
            if errs:
                raise OpenEMRError(f"OpenEMR returned errors: {errs}")
            data = body["data"]
            return data if isinstance(data, list) else ([data] if data else [])
        if isinstance(body, list):
            return body
        return [body] if body else []

    # ---- Standard REST API (read only) ----
    def api(self, path: str, **params) -> list[dict]:
        return self._data(self._get(f"{self.s.api_url}/{path.lstrip('/')}", params or None))

    def patients(self) -> list[dict]:
        return self.api("patient")

    def encounters(self, patient_uuid: str) -> list[dict]:
        return self.api(f"patient/{patient_uuid}/encounter")

    def soap_notes(self, pid: str | int, eid: str | int) -> list[dict]:
        return self.api(f"patient/{pid}/encounter/{eid}/soap_note")

    def appointments(self, pid: str | int) -> list[dict]:
        return self.api(f"patient/{pid}/appointment")

    def practitioners(self) -> list[dict]:
        return self.api("practitioner")

    # ---- FHIR (public metadata; resource reads need FHIR scopes) ----
    def fhir_metadata(self) -> dict:
        resp = self.http.get(f"{self.s.fhir_url}/metadata", headers={"Accept": "application/fhir+json"})
        resp.raise_for_status()
        return resp.json()
