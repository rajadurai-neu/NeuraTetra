"""Pull draft notes from OpenEMR and assess the ones that changed.

Two sources: FHIR Clinical Notes (OpenEMR 7.0.3 only allows system tokens on FHIR) and the
Standard API SOAP form.

Read-only: it only calls GET endpoints. Unchanged drafts are skipped (content fingerprint),
so a poll every few minutes costs no AI calls unless a clinician edited something.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Protocol

from cnat_api.connectors.openemr.fhir_mapper import ref_id
from cnat_api.connectors.openemr.fhir_mapper import to_note_context as fhir_to_note
from cnat_api.connectors.openemr.mapper import encounter_id, practitioner_index
from cnat_api.connectors.openemr.mapper import to_note_context as soap_to_note
from cnat_api.services.assessments import AssessmentService
from cnat_engine.domain import NoteContext

log = logging.getLogger("cnat.openemr_sync")
ACTOR = "system:openemr-sync"


class OpenEMRReader(Protocol):
    """Standard REST API reads (OpenEMR versions whose Standard API accepts system tokens)."""
    def patients(self) -> list[dict]: ...
    def encounters(self, patient_uuid: str) -> list[dict]: ...
    def soap_notes(self, pid, eid) -> list[dict]: ...
    def appointments(self, pid) -> list[dict]: ...
    def practitioners(self) -> list[dict]: ...


class FhirReader(Protocol):
    def fhir_search(self, resource: str, max_pages: int = 50, **params) -> list[dict]: ...
    def fhir_read(self, resource: str, rid: str) -> dict: ...


class NoteSource(Protocol):
    """Where draft notes come from. Returns notes (without prior-note texts) and per-note failures."""
    def fetch(self, since: date) -> tuple[list[NoteContext], list[str]]: ...


class StandardApiSource:
    """SOAP form via the Standard REST API: patients -> encounters -> soap_note."""

    def __init__(self, reader: OpenEMRReader, default_program: str | None = None):
        self.reader, self.default_program = reader, default_program

    def fetch(self, since: date) -> tuple[list[NoteContext], list[str]]:
        notes, failures = [], []
        practitioners = practitioner_index(self.reader.practitioners())
        for patient in self.reader.patients():
            pid, puuid = patient.get("pid"), patient.get("uuid")
            recent = [e for e in self.reader.encounters(puuid) if str(e.get("date", ""))[:10] >= since.isoformat()]
            if not recent:
                continue
            appointments = self.reader.appointments(pid)
            for enc in recent:
                for soap in self.reader.soap_notes(pid, encounter_id(enc)):
                    try:
                        notes.append(soap_to_note(patient=patient, encounter=enc, soap=soap,
                                                  practitioners=practitioners, appointments=appointments,
                                                  prior_texts=[], default_program=self.default_program))
                    except Exception as e:  # one bad record shouldn't stop the run
                        failures.append(f"soap {soap.get('id')}: {str(e)[:200]}")
        return notes, failures


class FhirSource:
    """Clinical Notes via FHIR (DocumentReference, category clinical-note). Works with system scopes."""

    def __init__(self, reader: FhirReader, default_program: str | None = None):
        self.reader, self.default_program = reader, default_program

    def fetch(self, since: date) -> tuple[list[NoteContext], list[str]]:
        notes, failures = [], []
        docs = self.reader.fhir_search("DocumentReference", category="clinical-note", date=f"ge{since.isoformat()}")
        cache: dict[tuple[str, str], dict | None] = {}

        def read(resource: str, rid: str | None) -> dict | None:
            if not rid:
                return None
            key = (resource, rid)
            if key not in cache:
                try:
                    cache[key] = self.reader.fhir_read(resource, rid)
                except Exception as e:  # a missing linked record shouldn't drop the note
                    log.warning("Could not read %s/%s: %s", resource, rid, e)
                    cache[key] = None
            return cache[key]

        for doc in docs:
            if doc.get("status") == "entered-in-error" or str(doc.get("date", ""))[:10] < since.isoformat():
                continue
            try:
                encounter = read("Encounter", ref_id(((doc.get("context") or {}).get("encounter") or [{}])[0], "Encounter"))
                org = read("Organization", ref_id((encounter or {}).get("serviceProvider"), "Organization"))
                author = next((a for a in doc.get("author") or [] if a), None)
                notes.append(fhir_to_note(
                    doc=doc, patient=read("Patient", ref_id(doc.get("subject"), "Patient")), encounter=encounter,
                    practitioner=read("Practitioner", ref_id(author, "Practitioner")),
                    clinic=(org or {}).get("name"), default_program=self.default_program))
            except Exception as e:
                failures.append(f"DocumentReference {doc.get('id')}: {str(e)[:200]}")
        return notes, failures


@dataclass
class SyncReport:
    started_at: datetime
    finished_at: datetime | None = None
    ok: bool = True
    error: str | None = None
    patients: int = 0
    notes_seen: int = 0
    assessed: int = 0
    unchanged: int = 0
    failed: int = 0
    failures: list[str] = field(default_factory=list)


class OpenEMRSync:
    def __init__(self, source: NoteSource, service: AssessmentService, *, lookback_days: int = 14,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self.source, self.service = source, service
        self.lookback_days, self.clock = lookback_days, clock
        self.last: SyncReport | None = None
        self._lock = threading.Lock()

    def run_once(self) -> SyncReport:
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("A sync is already running.")
        report = SyncReport(started_at=self.clock())
        try:
            self._run(report)
        except Exception as e:  # connection/auth failure: whole run fails -> alert
            report.ok, report.error = False, str(e)[:500]
            log.error("OpenEMR sync failed: %s", e)
        finally:
            report.finished_at = self.clock()
            self.last = report
            self._lock.release()
        if report.ok:
            log.info("OpenEMR sync: %s notes seen, %s assessed, %s unchanged, %s failed",
                     report.notes_seen, report.assessed, report.unchanged, report.failed)
        return report

    def _run(self, report: SyncReport) -> None:
        since = (self.clock() - timedelta(days=self.lookback_days)).date()
        notes, failures = self.source.fetch(since)
        report.failed += len(failures)
        report.failures += failures

        # Each client's notes oldest first, so each is compared with that client's earlier notes.
        by_client: dict[str, list[NoteContext]] = {}
        for n in notes:
            by_client.setdefault(n.client_id or n.note_id, []).append(n)
        report.patients = len(by_client)

        for client_notes in by_client.values():
            client_notes.sort(key=lambda n: (n.date_of_service or date.min, n.note_id))
            prior: list[str] = []
            for note in client_notes:
                report.notes_seen += 1
                try:
                    out = self.service.assess_note(note.model_copy(update={"prior_note_texts": prior[-5:]}),
                                                   actor=ACTOR, trigger="schedule")
                    if out.reused:
                        report.unchanged += 1
                    else:
                        report.assessed += 1
                except Exception as e:  # one bad note shouldn't stop the run
                    report.failed += 1
                    report.failures.append(f"{note.note_id}: {str(e)[:200]}")
                    log.warning("Could not assess OpenEMR note %s: %s", note.note_id, e)
                prior.append(note.text)


class SyncScheduler:
    """Runs a sync every N minutes on a background thread."""

    def __init__(self, sync: OpenEMRSync, minutes: float):
        self.sync, self.interval = sync, max(1.0, minutes) * 60
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="openemr-sync", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.sync.run_once()
            except RuntimeError:
                pass  # previous run still going
            self._stop.wait(self.interval)
