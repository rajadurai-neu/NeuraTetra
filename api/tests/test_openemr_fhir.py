"""FHIR intake: OpenEMR 7.0.3 Clinical Notes (DocumentReference) shapes, as seen on the dev server."""
import base64
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
import respx

from cnat_api.config import OpenEMRSettings
from cnat_api.connectors.openemr.client import OpenEMRClient
from cnat_api.connectors.openemr.fhir_mapper import to_note_context
from cnat_api.db.session import init_db, make_engine, make_session_factory
from cnat_api.jobs.openemr_sync import FhirSource, OpenEMRSync
from cnat_api.services.assessments import AssessmentService
from cnat_engine.ai.reviewer import NoAIReviewer
from cnat_engine.rules.ruleset import load_ruleset

RULES = load_ruleset(Path(__file__).resolve().parents[2] / "rulesets")
NOW = datetime(2026, 11, 12, 15, 0, tzinfo=timezone.utc)

GOOD = ('S: Client reported 2 panic attacks this week.\r\nO: Alert and oriented. Denies SI/HI.\r\n'
        'A: Progress toward Goal 1; practiced breathing.\r\nP: Continue weekly CBT. Next session 11/17/2026.')
WEAK = "S: Client said things are fine.\r\nO:\r\nA: Doing ok.\r\nP: Continue."
RISK = ('S: Client stated, "Sometimes I think everyone would be better off without me."\r\n'
        'O: Tearful.\r\nA: Client expressed thoughts of suicide in passing.\r\nP: Continue weekly therapy.')


def docref(i, day, text, author=None, docstatus=None):
    d = {"resourceType": "DocumentReference", "id": f"doc-{i}", "status": "current",
         "category": [{"coding": [{"code": "clinical-note"}]}],
         "subject": {"reference": "Patient/pat-1"}, "date": f"2026-11-{day}T00:00:00+00:00",
         "author": [author],
         "context": {"encounter": [{"reference": "Encounter/enc-1"}], "period": {"start": "2008-04-07T08:15:31+00:00"}},
         "content": [{"attachment": {"contentType": "text/plain", "data": base64.b64encode(text.encode()).decode()}}]}
    if docstatus:
        d["docStatus"] = docstatus
    return d


PATIENT = {"resourceType": "Patient", "id": "pat-1", "identifier": [{"type": {"coding": [{"code": "PT"}]}, "value": "21"}]}
ENCOUNTER = {"resourceType": "Encounter", "id": "enc-1", "period": {"start": "2008-04-07T08:15:31+00:00"},
             "serviceProvider": {"reference": "Organization/org-1"}}
ORG = {"resourceType": "Organization", "id": "org-1", "name": "Clinic A"}
PRAC = {"resourceType": "Practitioner", "id": "pr-1", "name": [{"given": ["Synthetic"], "family": "Clinician"}],
        "qualification": [{"code": {"text": "LCSW"}}]}


class FakeFhir:
    def __init__(self, docs):
        self.docs = docs
        self.resources = {("Patient", "pat-1"): PATIENT, ("Encounter", "enc-1"): ENCOUNTER,
                          ("Organization", "org-1"): ORG, ("Practitioner", "pr-1"): PRAC}
        self.searches = []

    def fhir_search(self, resource, max_pages=50, **params):
        self.searches.append((resource, params))
        return self.docs

    def fhir_read(self, resource, rid):
        return self.resources[(resource, rid)]


@pytest.fixture
def svc():
    engine = make_engine("sqlite://")
    init_db(engine)
    return AssessmentService(make_session_factory(engine), RULES, NoAIReviewer(), clock=lambda: NOW)


def test_mapper_uses_note_date_text_sections_and_chart_number():
    n = to_note_context(doc=docref(1, 10, GOOD), patient=PATIENT, encounter=ENCOUNTER, practitioner=None,
                        clinic="Clinic A", default_program="Adult Outpatient")
    assert n.note_id == "openemr:docref:doc-1" and n.client_id == "21" and n.clinic == "Clinic A"
    assert str(n.date_of_service) == "2026-11-10"            # the note's date, not the (old) encounter date
    assert "\r" not in n.text and n.sections["plan"].startswith("Continue weekly CBT")
    assert {"clinician_name", "start_time", "modality"} <= set(n.unavailable_fields)   # no author / times
    assert not n.is_signed


def test_mapper_reads_author_when_present():
    n = to_note_context(doc=docref(1, 10, GOOD, author={"reference": "Practitioner/pr-1"}, docstatus="final"),
                        patient=PATIENT, encounter=ENCOUNTER, practitioner=PRAC, clinic=None)
    assert n.clinician_name == "Synthetic Clinician" and n.clinician_credentials == "LCSW"
    assert "clinician_name" not in n.unavailable_fields and n.is_signed


def test_sync_assesses_three_notes_with_expected_results(svc):
    old = {**docref(4, 10, GOOD), "date": "2026-10-01T00:00:00+00:00"}   # outside the 14-day lookback
    fake = FakeFhir([docref(1, 10, GOOD), docref(2, 11, WEAK), docref(3, 12, RISK), old])
    sync = OpenEMRSync(FhirSource(fake, "Adult Outpatient"), svc, lookback_days=14, clock=lambda: NOW)
    rep = sync.run_once()
    assert rep.ok and rep.notes_seen == 3 and rep.assessed == 3 and rep.patients == 1
    assert fake.searches[0] == ("DocumentReference", {"category": "clinical-note", "date": "ge2026-10-29"})

    def result(i):
        return svc.get_results(f"openemr:docref:doc-{i}", viewer="t")

    weak, risk = result(2), result(3)
    assert "SOAP-SECTIONS" in {f.rule_id for f in weak.findings}
    assert "OMH-RISK-SAFETY" in {f.rule_id for f in risk.findings}
    assert risk.status.value == "does_not_meet_standards"
    for a in (result(1), weak, risk):   # fields FHIR can't supply never create false issues
        assert not {"P599-TIMES", "P599-CLINICIAN", "P599-MODALITY", "P599-TP-ACTIVE"} & {f.rule_id for f in a.findings}

    assert sync.run_once().unchanged == 3                        # re-poll: nothing changed, no re-assessment


def test_missing_linked_record_does_not_drop_note(svc):
    fake = FakeFhir([docref(1, 10, GOOD)])
    del fake.resources[("Encounter", "enc-1")]
    rep = OpenEMRSync(FhirSource(fake), svc, clock=lambda: NOW).run_once()
    assert rep.assessed == 1 and rep.failed == 0


@respx.mock
def test_fhir_search_follows_next_links_only_on_same_server():
    s = OpenEMRSettings(base_url="https://emr.example", client_id="c")
    base = s.fhir_url

    class Auth:
        def token(self):
            return "t"

        def invalidate(self):
            pass

    pages = [
        httpx.Response(200, json={"resourceType": "Bundle", "entry": [{"resource": {"id": "a"}}],
                                  "link": [{"relation": "next", "url": f"{base}/DocumentReference?page=2"}]}),
        httpx.Response(200, json={"resourceType": "Bundle", "entry": [{"resource": {"id": "b"}}],
                                  "link": [{"relation": "next", "url": "https://evil.example/steal"}]}),
    ]
    route = respx.get(url__startswith=f"{base}/DocumentReference").mock(side_effect=pages)
    evil = respx.get(url__startswith="https://evil.example").mock(return_value=httpx.Response(200, json={}))
    client = OpenEMRClient(s, auth=Auth(), http=httpx.Client())
    assert [r["id"] for r in client.fhir_search("DocumentReference")] == ["a", "b"]
    assert route.call_count == 2 and not evil.called
