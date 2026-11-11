"""Pull draft SOAP notes from OpenEMR and assess the ones that changed.

Read-only: it only calls GET endpoints. Unchanged drafts are skipped (content fingerprint),
so a poll every few minutes costs no AI calls unless a clinician edited something.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Protocol

from cnat_api.connectors.openemr.mapper import encounter_id, practitioner_index, to_note_context
from cnat_api.services.assessments import AssessmentService

log = logging.getLogger("cnat.openemr_sync")
ACTOR = "system:openemr-sync"


class OpenEMRReader(Protocol):
    def patients(self) -> list[dict]: ...
    def encounters(self, patient_uuid: str) -> list[dict]: ...
    def soap_notes(self, pid, eid) -> list[dict]: ...
    def appointments(self, pid) -> list[dict]: ...
    def practitioners(self) -> list[dict]: ...


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
    def __init__(self, reader: OpenEMRReader, service: AssessmentService, *, lookback_days: int = 14,
                 default_program: str | None = None,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self.reader, self.service = reader, service
        self.lookback_days, self.default_program, self.clock = lookback_days, default_program, clock
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
        since = (self.clock() - timedelta(days=self.lookback_days)).date().isoformat()
        practitioners = practitioner_index(self.reader.practitioners())

        for patient in self.reader.patients():
            report.patients += 1
            pid, puuid = patient.get("pid"), patient.get("uuid")
            recent = [e for e in self.reader.encounters(puuid) if str(e.get("date", ""))[:10] >= since]
            if not recent:
                continue
            appointments = self.reader.appointments(pid)

            # Gather this client's SOAP notes oldest first so each can be compared with earlier ones.
            items = []
            for enc in recent:
                for soap in self.reader.soap_notes(pid, encounter_id(enc)):
                    items.append((str(enc.get("date", "")), enc, soap))
            items.sort(key=lambda x: x[0])

            prior_texts: list[str] = []
            for _, enc, soap in items:
                report.notes_seen += 1
                try:
                    note = to_note_context(patient=patient, encounter=enc, soap=soap,
                                           practitioners=practitioners, appointments=appointments,
                                           prior_texts=list(prior_texts[-5:]),
                                           default_program=self.default_program)
                    prior_texts.append(note.text)
                    out = self.service.assess_note(note, actor=ACTOR, trigger="schedule")
                    if out.reused:
                        report.unchanged += 1
                    else:
                        report.assessed += 1
                except Exception as e:  # one bad note shouldn't stop the run
                    report.failed += 1
                    report.failures.append(f"soap {soap.get('id')}: {str(e)[:200]}")
                    log.warning("Could not assess OpenEMR SOAP note %s: %s", soap.get("id"), e)


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
