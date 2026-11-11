"""OpenEMR intake: mapping + sync, against a fake OpenEMR with realistic (synthetic) record shapes."""
import copy
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from cnat_api.connectors.openemr.mapper import OPENEMR_UNAVAILABLE, to_note_context
from cnat_api.db.session import init_db, make_engine, make_session_factory
from cnat_api.jobs.openemr_sync import OpenEMRSync
from cnat_api.services.assessments import AssessmentService
from cnat_api.web.app import create_app
from cnat_api.web.security import AuthSettings
from cnat_engine.ai.reviewer import NoAIReviewer
from cnat_engine.rules.ruleset import load_ruleset
from pathlib import Path

RULES = load_ruleset(Path(__file__).resolve().parents[2] / "rulesets")
NOW = datetime(2026, 11, 10, 15, 0, tzinfo=timezone.utc)

PATIENTS = [{"pid": "7", "uuid": "p-uuid-7", "pubpid": "SYN-007"}]
PRACTITIONERS = [
    {"id": "3", "fname": "Synthetic", "lname": "Clinician", "valedictory": "LCSW"},
    {"id": "4", "fname": "Synthetic", "lname": "Supervisor", "valedictory": "LCSW-R"},
]
ENCOUNTERS = {"p-uuid-7": [
    {"eid": "101", "date": "2026-11-02 00:00:00", "provider_id": "3", "supervisor_id": "4", "facility": "Clinic A"},
    {"eid": "102", "date": "2026-11-09 00:00:00", "provider_id": "3", "supervisor_id": "4", "facility": "Clinic A"},
    {"eid": "050", "date": "2026-08-01 00:00:00", "provider_id": "3", "facility": "Clinic A"},  # outside lookback
]}
SOAP = {
    ("7", "101"): [{"id": "901", "date": "2026-11-02", "user": "sclin",
                    "subjective": "Client reported 3 panic attacks this week.",
                    "objective": "Anxious affect, good eye contact. Denies SI/HI.",
                    "assessment": "Some progress toward Goal 1; practiced breathing retraining.",
                    "plan": "Continue weekly CBT. Next session 11/09/2026."}],
    ("7", "102"): [{"id": "902", "date": "2026-11-09", "user": "sclin",
                    "subjective": "Client said things are fine.",
                    "objective": "",
                    "assessment": "Doing fine.",
                    "plan": "Continue."}],
    ("7", "050"): [{"id": "800", "date": "2026-08-01", "subjective": "old", "objective": "old", "assessment": "old", "plan": "old"}],
}
APPTS = {"7": [{"pc_eid": "1", "pc_aid": "3", "pc_eventDate": "2026-11-09", "pc_startTime": "10:00:00", "pc_endTime": "10:55:00"}]}


class FakeOpenEMR:
    def __init__(self):
        self.soap = copy.deepcopy(SOAP)
        self.fail_patients = False

    def patients(self):
        if self.fail_patients:
            raise ConnectionError("OpenEMR unreachable")
        return PATIENTS

    def encounters(self, puuid):
        return ENCOUNTERS[puuid]

    def soap_notes(self, pid, eid):
        return self.soap.get((str(pid), str(eid)), [])

    def appointments(self, pid):
        return APPTS.get(str(pid), [])

    def practitioners(self):
        return PRACTITIONERS


@pytest.fixture
def env():
    engine = make_engine("sqlite://")
    init_db(engine)
    svc = AssessmentService(make_session_factory(engine), RULES, NoAIReviewer(), clock=lambda: NOW)
    fake = FakeOpenEMR()
    sync = OpenEMRSync(fake, svc, lookback_days=14, default_program="Adult Outpatient", clock=lambda: NOW)
    return svc, fake, sync


def test_mapper_builds_note_with_people_times_and_sections():
    from cnat_api.connectors.openemr.mapper import practitioner_index
    note = to_note_context(patient=PATIENTS[0], encounter=ENCOUNTERS["p-uuid-7"][1], soap=SOAP[("7", "102")][0],
                           practitioners=practitioner_index(PRACTITIONERS), appointments=APPTS["7"],
                           prior_texts=[], default_program="Adult Outpatient")
    assert note.note_id == "openemr:soap:902" and note.source == "openemr"
    assert note.clinician_name == "Synthetic Clinician" and note.clinician_credentials == "LCSW"
    assert note.supervisor == "Synthetic Supervisor" and note.client_id == "SYN-007"
    assert note.duration_minutes == 55 and str(note.date_of_service) == "2026-11-09"
    assert note.sections["assessment"] == "Doing fine." and note.text.startswith("S: ")
    assert set(OPENEMR_UNAVAILABLE) <= set(note.unavailable_fields)


def test_sync_assesses_recent_notes_and_skips_old(env):
    svc, _, sync = env
    rep = sync.run_once()
    assert rep.ok and rep.notes_seen == 2 and rep.assessed == 2 and rep.failed == 0
    ids = {q["note_id"] for q in svc.queue()}
    assert ids == {"openemr:soap:901", "openemr:soap:902"}


def test_unavailable_fields_do_not_create_false_issues(env):
    svc, _, sync = env
    sync.run_once()
    a = svc.get_results("openemr:soap:902", viewer="t")
    rule_ids = {f.rule_id for f in a.findings}
    for skipped in ("P599-MODALITY", "P599-SERVICE-TYPE", "P599-PARTICIPANTS", "P599-TP-ACTIVE"):
        assert skipped not in rule_ids
    # Real gaps are still reported: note 901 has no appointment -> no start/end time
    assert "P599-TIMES" in {f.rule_id for f in svc.get_results("openemr:soap:901", viewer="t").findings}


def test_second_poll_skips_unchanged_and_reassesses_edits(env):
    svc, fake, sync = env
    sync.run_once()
    rep = sync.run_once()
    assert rep.unchanged == 2 and rep.assessed == 0
    fake.soap[("7", "102")][0]["objective"] = "Affect brighter; engaged throughout."
    rep = sync.run_once()
    assert rep.assessed == 1 and rep.unchanged == 1
    assert len(svc.history("openemr:soap:902")) == 2


def test_connection_failure_is_reported_not_raised(env):
    _, fake, sync = env
    fake.fail_patients = True
    rep = sync.run_once()
    assert not rep.ok and "unreachable" in rep.error
    assert sync.last is rep


def test_admin_endpoints(env):
    svc, _, sync = env
    client = TestClient(create_app(svc, AuthSettings(dev_auth=True), openemr_sync=sync))
    admin = {"X-Dev-User": "a", "X-Dev-Role": "admin"}
    assert client.get("/api/admin/openemr/status", headers=admin).json()["last_run"] is None
    r = client.post("/api/admin/openemr/sync", headers=admin).json()
    assert r["last_run"]["assessed"] == 2 and r["last_run"]["finished_at_display"].endswith(" ET")
    clin = {"X-Dev-User": "Synthetic Clinician", "X-Dev-Role": "clinician"}
    assert client.post("/api/admin/openemr/sync", headers=clin).status_code == 403
    # synced notes are visible to their clinician
    assert len(client.get("/api/queue", headers=clin).json()) == 2


def test_admin_endpoints_when_not_configured(env):
    svc, _, _ = env
    client = TestClient(create_app(svc, AuthSettings(dev_auth=True)))
    assert client.get("/api/admin/openemr/status", headers={"X-Dev-User": "a", "X-Dev-Role": "admin"}).status_code == 404
