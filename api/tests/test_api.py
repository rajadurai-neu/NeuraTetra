import base64
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cnat_api.web.app import create_app
from cnat_api.web.security import AuthSettings
from cnat_api.db.session import init_db, make_engine, make_session_factory
from cnat_engine.ai.reviewer import NoAIReviewer
from cnat_engine.rules.ruleset import load_ruleset
from cnat_api.services.assessments import AssessmentService

SAMPLES = Path(__file__).resolve().parents[2] / "rulesets" / "samples"
CLIN = {"X-Dev-User": "Synthetic Clinician", "X-Dev-Role": "clinician"}
OTHER = {"X-Dev-User": "Someone Else", "X-Dev-Role": "clinician"}
SUP = {"X-Dev-User": "Synthetic Supervisor", "X-Dev-Role": "supervisor"}
QA = {"X-Dev-User": "QA Person", "X-Dev-Role": "qa"}
NOW = datetime(2026, 11, 10, 15, 0, tzinfo=timezone.utc)  # fixed, so the 72-hour timeliness rule is stable


@pytest.fixture
def client():
    engine = make_engine("sqlite://")
    init_db(engine)
    svc = AssessmentService(make_session_factory(engine), load_ruleset(SAMPLES.parent), NoAIReviewer(), clock=lambda: NOW)
    return TestClient(create_app(svc, AuthSettings(dev_auth=True)))


def note(name):
    return json.loads((SAMPLES / f"{name}.json").read_text())["note"]


def test_requires_sign_in(client):
    assert client.get("/api/queue").status_code == 401
    assert client.get("/api/health").status_code == 200


def test_dev_auth_off_rejects_dev_headers():
    engine = make_engine("sqlite://")
    init_db(engine)
    svc = AssessmentService(make_session_factory(engine), load_ruleset(SAMPLES.parent), NoAIReviewer())
    c = TestClient(create_app(svc, AuthSettings(dev_auth=False)))
    assert c.get("/api/me", headers=CLIN).status_code == 401


def test_easy_auth_header_is_read(client):
    principal = {"auth_typ": "aad", "name_typ": "name", "role_typ": "roles",
                 "claims": [{"typ": "name", "val": "QA Person"}, {"typ": "roles", "val": "QA"}]}
    h = {"X-MS-CLIENT-PRINCIPAL": base64.b64encode(json.dumps(principal).encode()).decode()}
    assert client.get("/api/me", headers=h).json() == {"name": "QA Person", "roles": ["qa"]}


def test_assess_returns_fix_first_and_et_time(client):
    r = client.post("/api/notes/assess", json=note("poor_progress"), headers=CLIN)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["assessment"]["status"] == "does_not_meet_standards"
    assert body["fix_first"][0]["severity"] == "critical"
    assert body["assessed_at_display"].endswith(" ET")


def test_pasted_note_gets_id_and_owner(client):
    r = client.post("/api/notes/assess", json={"text": "S: ok\nO: ok\nA: ok\nP: ok"}, headers=CLIN)
    nid = r.json()["assessment"]["note_id"]
    assert nid.startswith("manual:")
    assert client.get(f"/api/notes/{nid}/assessment", headers=CLIN).status_code == 200


def test_clinician_cannot_submit_or_see_others_notes(client):
    client.post("/api/notes/assess", json=note("poor_progress"), headers=CLIN)
    assert client.get("/api/notes/S2/assessment", headers=OTHER).status_code == 404
    assert client.post("/api/notes/assess", json=note("poor_progress"), headers=OTHER).status_code == 403


def test_supervisor_and_qa_scopes(client):
    client.post("/api/notes/assess", json=note("poor_progress"), headers=CLIN)
    client.post("/api/notes/assess", json={**note("good_progress"), "supervisor": "Other Sup"}, headers=CLIN)
    sup_ids = {q["note_id"] for q in client.get("/api/queue", headers=SUP).json()}
    qa_ids = {q["note_id"] for q in client.get("/api/queue", headers=QA).json()}
    assert sup_ids == {"S2"} and qa_ids == {"S1", "S2"}
    # a clinician can't widen their scope with filters
    assert {q["note_id"] for q in client.get("/api/queue?clinician=Someone", headers=CLIN).json()} == {"S1", "S2"}
    assert client.get("/api/queue?status=meets_standards", headers=QA).json()[0]["note_id"] == "S1"


def test_not_applicable_flow_and_history(client):
    client.post("/api/notes/assess", json=note("telehealth_missing"), headers=CLIN)
    r = client.post("/api/notes/S3/findings/OMH-TELE-CONSENT/not-applicable",
                    json={"reason": "Consent on file"}, headers=CLIN)
    assert r.status_code == 200
    assert "OMH-TELE-CONSENT" in r.json()["changes"]["resolved"]
    assert client.post("/api/notes/S3/findings/OMH-TELE-LOCATION/not-applicable",
                       json={"reason": ""}, headers=CLIN).status_code == 422
    r = client.delete("/api/notes/S3/findings/OMH-TELE-CONSENT/not-applicable", headers=CLIN)
    assert "OMH-TELE-CONSENT" in r.json()["changes"]["new"]
    hist = client.get("/api/notes/S3/history", headers=CLIN).json()
    assert len(hist) == 3 and hist[-1]["is_current"]


def test_batch_and_upload(client):
    r = client.post("/api/notes/assess-batch", json=[note("good_progress"), note("cloned")], headers=QA)
    assert [x["ok"] for x in r.json()] == [True, True]
    files = [("files", ("a.txt", b"S: x\nO: y\nA: z\nP: w", "text/plain")),
             ("files", ("b.pdf", b"%PDF", "application/pdf"))]
    r = client.post("/api/notes/upload", files=files, data={"program": "Adult Outpatient"}, headers=CLIN)
    assert [x["ok"] for x in r.json()] == [True, False]


def test_signed_with_open_issues(client):
    client.post("/api/notes/assess", json=note("poor_progress"), headers=CLIN)
    r = client.post("/api/notes/S2/signed", headers=CLIN)
    assert r.json()["signed_with_open_issues"] is True
    assert client.get("/api/queue?signed=true", headers=CLIN).json()[0]["signed_with_open_issues"] is True


def test_roles_endpoint_guard(client):
    assert client.get("/api/admin/ping", headers=QA).status_code == 403
    assert client.get("/api/admin/ping", headers={"X-Dev-User": "a", "X-Dev-Role": "admin"}).status_code == 200


def test_ruleset_visible(client):
    body = client.get("/api/rulesets/current", headers=CLIN).json()
    assert body["version"].startswith("2026.11.0") and len(body["quality_measures"]) == 8


def test_report_summary_scoped(client):
    client.post("/api/notes/assess", json=note("poor_progress"), headers=CLIN)
    client.post("/api/notes/assess", json=note("good_progress"), headers=CLIN)
    body = client.get("/api/reports/summary", headers=QA).json()
    assert body["notes_assessed"] == 2
    assert body["first_pass_rate"] == 50.0
    assert {s["status"]: s["count"] for s in body["status_counts"]}["does_not_meet_standards"] == 1
    assert body["top_issues"][0]["count"] >= 1 and body["trend"][0]["notes"] == 2
    assert client.get("/api/reports/summary", headers=OTHER).json()["notes_assessed"] == 0
