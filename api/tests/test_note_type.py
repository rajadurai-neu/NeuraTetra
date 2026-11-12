"""Note type: detected from the EHR, correctable by users, correction survives syncs (spec A2.8)."""
import base64
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cnat_api.connectors.openemr.fhir_mapper import detect_note_type
from cnat_api.db.session import init_db, make_engine, make_session_factory
from cnat_api.jobs.openemr_sync import FhirSource, OpenEMRSync
from cnat_api.services.assessments import AssessmentService
from cnat_api.web.app import create_app
from cnat_api.web.security import AuthSettings
from cnat_engine.ai.reviewer import NoAIReviewer
from cnat_engine.domain import NoteType
from cnat_engine.rules.ruleset import load_ruleset

RULES = load_ruleset(Path(__file__).resolve().parents[2] / "rulesets")
NOW = datetime(2026, 11, 12, 15, 0, tzinfo=timezone.utc)
QA = {"X-Dev-User": "QA Person", "X-Dev-Role": "qa"}
TEXT = "S: Client shared with the group.\nO: Engaged.\nA: Practiced skills.\nP: Attend next group."


def doc(type_=None):
    d = {"resourceType": "DocumentReference", "id": "d1", "status": "current",
         "subject": {"reference": "Patient/p1"}, "date": "2026-11-11T00:00:00+00:00", "author": [None],
         "context": {"encounter": [{"reference": "Encounter/e1"}]},
         "content": [{"attachment": {"contentType": "text/plain", "data": base64.b64encode(TEXT.encode()).decode()}}]}
    if type_:
        d["type"] = type_
    return d


UNK = {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/v3-NullFlavor", "code": "UNK", "display": "unknown"}]}


def enc(reason):
    return {"resourceType": "Encounter", "id": "e1", "class": {"code": "AMB"}, "reasonCode": [{"text": reason}]}


@pytest.mark.parametrize("type_, reason, expected, source_starts", [
    (UNK, "Group therapy", NoteType.PROGRESS_GROUP, "EHR visit reason"),
    (UNK, "Family session", NoteType.PROGRESS_FAMILY, "EHR visit reason"),
    (UNK, "Individual therapy", NoteType.PROGRESS_INDIVIDUAL, "EHR visit reason"),
    (UNK, "Psychiatric evaluation", NoteType.PSYCHIATRIC_EVALUATION, "EHR visit reason"),
    (UNK, "Treatment plan review", NoteType.TREATMENT_PLAN_REVIEW, "EHR visit reason"),
    (UNK, "Med check", NoteType.MEDICATION_MANAGEMENT, "EHR visit reason"),
    ({"text": "Discharge Summary"}, "Individual therapy", NoteType.DISCHARGE_SUMMARY, "EHR note type"),
    ({"coding": [{"system": "http://loinc.org", "code": "18842-5"}]}, "", NoteType.DISCHARGE_SUMMARY, "EHR note code"),
    # a generic LOINC "progress note" loses to a specific visit reason
    ({"coding": [{"system": "http://loinc.org", "code": "11506-3"}]}, "Group therapy", NoteType.PROGRESS_GROUP, "EHR visit reason"),
    (UNK, "Check up", NoteType.PROGRESS_INDIVIDUAL, "default"),
])
def test_detect_note_type(type_, reason, expected, source_starts):
    t, src = detect_note_type(doc(type_), enc(reason))
    assert t == expected and src.startswith(source_starts)


def test_configurable_default():
    t, src = detect_note_type(doc(UNK), enc("Check up"), default=NoteType.CRISIS_SAFETY)
    assert t == NoteType.CRISIS_SAFETY and src.startswith("default")


class FakeFhir:
    def __init__(self, reason):
        self.res = {("DocumentReference", "d1"): doc(UNK), ("Encounter", "e1"): enc(reason),
                    ("Patient", "p1"): {"id": "p1"}}

    def fhir_search(self, resource, max_pages=50, **params):
        return [self.res[("DocumentReference", "d1")]] if resource == "DocumentReference" else []

    def fhir_read(self, resource, rid):
        return self.res[(resource, rid)]


@pytest.fixture
def env():
    engine = make_engine("sqlite://")
    init_db(engine)
    svc = AssessmentService(make_session_factory(engine), RULES, NoAIReviewer(), clock=lambda: NOW)
    sync = OpenEMRSync(FhirSource(FakeFhir("Group therapy")), svc, clock=lambda: NOW)
    client = TestClient(create_app(svc, AuthSettings(dev_auth=True), openemr_sync=sync))
    return svc, sync, client


NID = "openemr%3Adocref%3Ad1"


def test_sync_sets_type_and_source(env):
    svc, sync, client = env
    sync.run_once()
    info = client.get(f"/api/notes/{NID}", headers=QA).json()
    assert info["note_type"] == "progress_group" and info["note_type_source"] == "EHR visit reason: Group therapy"
    assert info["corrected"] is False


def test_user_correction_rescored_audited_and_kept_by_sync(env):
    svc, sync, client = env
    sync.run_once()
    r = client.put(f"/api/notes/{NID}/note-type", headers=QA,
                   json={"note_type": "progress_family", "reason": "Family session, not group"})
    assert r.status_code == 200
    info = client.get(f"/api/notes/{NID}", headers=QA).json()
    assert info["note_type"] == "progress_family" and info["corrected"]
    assert info["correction"]["by"] == "QA Person" and info["correction"]["detected_type"] == "progress_group"
    assert info["correction"]["at_display"].endswith(" ET")
    assert svc.history("openemr:docref:d1")[-1]["trigger"] == "type_change"
    from cnat_api.db.repository import audit_trail
    with svc.sessions() as s:
        assert any(a.action == "note_type_changed" for a in audit_trail(s, "note", "openemr:docref:d1"))

    # The next sync sees the same EHR note: the correction stays and nothing is re-assessed.
    rep = sync.run_once()
    assert rep.unchanged == 1 and rep.assessed == 0
    assert client.get(f"/api/notes/{NID}", headers=QA).json()["note_type"] == "progress_family"
    # Re-assess (fetches the latest from the EHR) keeps it too.
    client.post(f"/api/notes/{NID}/reassess", headers=QA)
    assert client.get(f"/api/notes/{NID}", headers=QA).json()["note_type"] == "progress_family"

    # Undo: back to what the EHR said.
    r = client.delete(f"/api/notes/{NID}/note-type", headers=QA)
    assert r.status_code == 200
    info = client.get(f"/api/notes/{NID}", headers=QA).json()
    assert info["note_type"] == "progress_group" and not info["corrected"]
    assert info["note_type_source"] == "EHR visit reason: Group therapy"


def test_correction_validation_and_access(env):
    svc, sync, client = env
    sync.run_once()
    assert client.put(f"/api/notes/{NID}/note-type", headers=QA,
                      json={"note_type": "progress_family", "reason": "x"}).status_code == 422
    assert client.put(f"/api/notes/{NID}/note-type", headers=QA,
                      json={"note_type": "not_a_type", "reason": "because"}).status_code == 422
    assert client.put(f"/api/notes/{NID}/note-type", headers=QA,
                      json={"note_type": "progress_group", "reason": "same type"}).status_code == 400
    assert client.delete(f"/api/notes/{NID}/note-type", headers=QA).status_code == 400
    other = {"X-Dev-User": "Someone", "X-Dev-Role": "clinician"}
    assert client.put(f"/api/notes/{NID}/note-type", headers=other,
                      json={"note_type": "progress_family", "reason": "not mine"}).status_code == 404


def test_rules_endpoint_lists_required_elements(env):
    _, _, client = env
    body = client.get("/api/rulesets/current", headers=QA).json()
    assert set(body["required_elements"]) == {t.value for t in NoteType}
    assert body["quality_measures"][1]["applies_to"]["note_types"]
