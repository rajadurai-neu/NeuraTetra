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

from cnat_api.connectors.openemr.fhir_mapper import find_appointment, note_text, ref_id
from cnat_api.connectors.openemr.fhir_mapper import to_note_context as fhir_to_note
from cnat_api.connectors.openemr.mapper import encounter_id, practitioner_index
from cnat_api.connectors.openemr.mapper import to_note_context as soap_to_note
from cnat_api.services.assessments import AssessmentService
from cnat_engine.domain import NoteContext, NoteType

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
    label = "SOAP form (Standard API)"

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
    label = "Clinical Notes (FHIR)"
    PREFIX = "openemr:docref:"

    def __init__(self, reader: FhirReader, default_program: str | None = None,
                 default_note_type: NoteType = NoteType.PROGRESS_INDIVIDUAL):
        self.reader, self.default_program = reader, default_program
        self.default_note_type = NoteType(default_note_type)

    # ---- shared helpers (one cache per fetch) ----
    def _reader(self):
        cache: dict[tuple, object] = {}

        def read(resource: str, rid: str | None) -> dict | None:
            if not rid:
                return None
            key = ("read", resource, rid)
            if key not in cache:
                try:
                    cache[key] = self.reader.fhir_read(resource, rid)
                except Exception as e:  # a missing linked record shouldn't drop the note
                    log.warning("Could not read %s/%s: %s", resource, rid, e)
                    cache[key] = None
            return cache[key]

        def appointments(patient_id: str | None) -> list[dict]:
            if not patient_id:
                return []
            key = ("appts", patient_id)
            if key not in cache:
                try:
                    cache[key] = self.reader.fhir_search("Appointment", patient=patient_id)
                except Exception as e:  # times are optional: without them the time rules are skipped
                    log.warning("Could not read appointments for %s: %s", patient_id, e)
                    cache[key] = []
            return cache[key]

        return read, appointments

    def _build(self, doc: dict, read, appointments) -> NoteContext:
        encounter = read("Encounter", ref_id(((doc.get("context") or {}).get("encounter") or [{}])[0], "Encounter"))
        org = read("Organization", ref_id((encounter or {}).get("serviceProvider"), "Organization"))
        author_id = next((ref_id(a, "Practitioner") for a in doc.get("author") or [] if a), None)
        patient_id = ref_id(doc.get("subject"), "Patient")
        dos = str(doc.get("date", ""))[:10]
        appt = find_appointment(appointments(patient_id), date.fromisoformat(dos) if dos else None, author_id)
        return fhir_to_note(
            doc=doc, patient=read("Patient", patient_id), encounter=encounter,
            practitioner=read("Practitioner", author_id), clinic=(org or {}).get("name"),
            default_program=self.default_program, appointment=appt, default_note_type=self.default_note_type)

    # ---- NoteSource ----
    def fetch(self, since: date) -> tuple[list[NoteContext], list[str]]:
        notes, failures = [], []
        docs = self.reader.fhir_search("DocumentReference", category="clinical-note", date=f"ge{since.isoformat()}")
        read, appointments = self._reader()
        for doc in docs:
            if doc.get("status") == "entered-in-error" or str(doc.get("date", ""))[:10] < since.isoformat():
                continue
            try:
                notes.append(self._build(doc, read, appointments))
            except Exception as e:
                failures.append(f"DocumentReference {doc.get('id')}: {str(e)[:200]}")
        return notes, failures

    def fetch_one(self, note_id: str) -> NoteContext | None:
        """Latest copy of one note from OpenEMR, with the client's earlier notes for the clone check."""
        if not note_id.startswith(self.PREFIX):
            return None
        read, appointments = self._reader()
        doc = self.reader.fhir_read("DocumentReference", note_id[len(self.PREFIX):])
        note = self._build(doc, read, appointments)
        patient_id = ref_id(doc.get("subject"), "Patient")
        prior = []
        if patient_id:
            others = self.reader.fhir_search("DocumentReference", patient=patient_id, category="clinical-note")
            earlier = sorted((d for d in others if d.get("id") != doc.get("id")
                              and str(d.get("date", "")) < str(doc.get("date", ""))),
                             key=lambda d: str(d.get("date", "")))
            prior = [t for t in (note_text(d) for d in earlier[-5:]) if t]
        return note.model_copy(update={"prior_note_texts": prior})


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
    skipped_signed: int = 0          # signed in the EHR: not assessed (drafts-only rule)
    mode: str = "drafts"             # drafts | retro (QA retrospective review of signed notes)
    failures: list[str] = field(default_factory=list)


class OpenEMRSync:
    def __init__(self, source: NoteSource, service: AssessmentService, *, lookback_days: int = 14,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self.source, self.service = source, service
        self.lookback_days, self.clock = lookback_days, clock
        self.last: SyncReport | None = None
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._lock.locked()

    def run_once(self, retro: bool = False) -> SyncReport:
        """Drafts only (default). retro=True: QA retrospective review of signed notes only."""
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("A sync is already running.")
        report = SyncReport(started_at=self.clock(), mode="retro" if retro else "drafts")
        try:
            self._run(report, retro)
        except Exception as e:  # connection/auth failure: whole run fails -> alert
            report.ok, report.error = False, str(e)[:500]
            log.error("OpenEMR sync failed: %s", e)
        finally:
            report.finished_at = self.clock()
            self.last = report
            self._lock.release()
        if report.ok:
            log.info("OpenEMR sync (%s): %s notes seen, %s assessed, %s unchanged, %s signed skipped, %s failed",
                     report.mode, report.notes_seen, report.assessed, report.unchanged,
                     report.skipped_signed, report.failed)
        return report

    def _run(self, report: SyncReport, retro: bool) -> None:
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
                    if note.is_signed and not retro:
                        # Drafts only. A draft CNAT already assessed is now signed: record it, so
                        # "signed with open issues" is reported.
                        report.skipped_signed += 1
                        self.service.note_signed_in_ehr(note.note_id)
                    elif retro and not note.is_signed:
                        pass                                   # retrospective review covers signed notes only
                    else:
                        out = self.service.assess_note(
                            note.model_copy(update={"prior_note_texts": prior[-5:]}), actor=ACTOR,
                            trigger="retro" if retro else "schedule", force=retro)
                        if out.reused:
                            report.unchanged += 1
                        else:
                            report.assessed += 1
                except Exception as e:  # one bad note shouldn't stop the run
                    report.failed += 1
                    report.failures.append(f"{note.note_id}: {str(e)[:200]}")
                    log.warning("Could not assess OpenEMR note %s: %s", note.note_id, e)
                prior.append(note.text)

    def fetch_latest(self, note_id: str) -> NoteContext | None:
        """Latest copy of one note from the EHR (on-demand re-assessment). None if the source can't."""
        fetch_one = getattr(self.source, "fetch_one", None)
        return fetch_one(note_id) if fetch_one else None


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
