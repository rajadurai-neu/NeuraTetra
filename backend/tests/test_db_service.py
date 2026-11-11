import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import inspect

from cnat.db import repository as repo
from cnat.db.session import init_db, make_engine, make_session_factory
from cnat.domain import NoteContext, Status
from cnat.engine.ai.reviewer import AIFinding, AIMeasure, AIReviewOutput, to_review_result
from cnat.engine.rules.ruleset import load_ruleset
from cnat.services.assessments import AssessmentService
from cnat.timefmt import fmt_et

SAMPLES = Path(__file__).parent / "samples"


class FakeReviewer:
    model, prompt_version = "fake-model", "v1"

    def __init__(self):
        self.calls = 0

    def review(self, note, measures):
        self.calls += 1
        out = AIReviewOutput(
            findings=[AIFinding(rule_id="AI-clear_plan", element="Plan", severity="warning",
                                issue="No next session.", evidence_quote="ABSENT",
                                suggestion="State the next session date.")],
            measure_scores=[AIMeasure(measure="clear_plan", score=1, finding="Vague")])
        return to_review_result(out, note, measures, self.model)


class Clock:
    def __init__(self):
        self.t = datetime(2026, 11, 10, 17, 0, tzinfo=timezone.utc)

    def __call__(self):
        self.t += timedelta(minutes=1)
        return self.t


@pytest.fixture
def svc():
    engine = make_engine("sqlite://")
    init_db(engine)
    rev = FakeReviewer()
    service = AssessmentService(make_session_factory(engine), load_ruleset(), rev, clock=Clock())
    service._rev = rev
    return service


def sample(name) -> NoteContext:
    return NoteContext(**json.loads((SAMPLES / f"{name}.json").read_text())["note"])


def test_tables_created(svc):
    names = set(inspect(svc.sessions.kw["bind"]).get_table_names())
    assert {"notes", "assessments", "findings", "measure_scores", "not_applicable",
            "ai_review_cache", "audit_log"} <= names


def test_assess_saves_current_result(svc):
    out = svc.assess_note(sample("poor_progress"), actor="clin1")
    assert out.assessment.status == Status.DOES_NOT_MEET
    stored = svc.get_results("S2", viewer="clin1")
    assert stored.model_dump(exclude={"assessed_at"}) == out.assessment.model_dump(exclude={"assessed_at"})


def test_unchanged_draft_is_not_reassessed(svc):
    svc.assess_note(sample("poor_progress"))
    again = svc.assess_note(sample("poor_progress"))
    assert again.reused and svc._rev.calls == 1
    assert len(svc.history("S2")) == 1


def test_edit_reassesses_and_tracks_resolved_issues(svc):
    note = sample("poor_progress")
    first = svc.assess_note(note)
    fixed = note.model_copy(update={"clinician_credentials": "LCSW", "participants": ["Client"]})
    second = svc.assess_note(fixed)
    assert {"P599-CREDENTIALS", "P599-PARTICIPANTS"} <= set(second.changes.resolved)
    assert "OMH-RISK-SAFETY" in second.changes.persisting
    hist = svc.history("S2")
    assert [h["is_current"] for h in hist] == [False, True]
    assert first.assessment_id != second.assessment_id
    assert svc._rev.calls == 1  # note text unchanged -> AI result reused from DB cache


def test_not_applicable_is_kept_and_rescored(svc):
    svc.assess_note(sample("telehealth_missing"))
    before = svc.get_results("S3", viewer="x")
    out = svc.mark_not_applicable("S3", "OMH-TELE-CONSENT", "Consent on file in intake packet", user="clin1")
    f = next(f for f in out.assessment.findings if f.rule_id == "OMH-TELE-CONSENT")
    assert f.not_applicable_reason == "Consent on file in intake packet"
    assert out.assessment.rules_checked == before.rules_checked - 1
    with pytest.raises(ValueError):
        svc.mark_not_applicable("S3", "OMH-TELE-LOCATION", "  ", user="clin1")


def test_signed_with_open_issues_recorded(svc):
    svc.assess_note(sample("poor_progress"))
    assert svc.record_signed("S2", user="clin1") is True
    with svc.sessions() as s:
        assert repo.get_note(s, "S2").signed_with_open_issues is True


def test_first_assessment_flag_for_reports(svc):
    note = sample("poor_progress")
    svc.assess_note(note)
    svc.assess_note(note, force=True)
    with svc.sessions() as s:
        assert [r.first_for_note for r in repo.assessment_history(s, "S2")] == [True, False]


def test_audit_log_records_actions(svc):
    svc.assess_note(sample("poor_progress"), actor="clin1")
    svc.get_results("S2", viewer="sup1")
    svc.record_signed("S2", user="clin1")
    with svc.sessions() as s:
        actions = [r.action for r in repo.audit_trail(s, "note", "S2")]
    assert actions == ["note_retrieved", "assessment_run", "results_viewed", "note_signed"]


def test_fix_first_lists_criticals_first(svc):
    svc.assess_note(sample("poor_progress"))
    items = svc.fix_first("S2")
    sev = [i["severity"] for i in items]
    assert sev == sorted(sev, key=["critical", "warning"].index)


def test_time_format_is_12h_eastern():
    assert fmt_et(datetime(2026, 10, 30, 18, 5, tzinfo=timezone.utc)) == "10/30/2026 2:05 PM ET"
    assert fmt_et(datetime(2026, 12, 1, 5, 0, tzinfo=timezone.utc)) == "12/01/2026 12:00 AM ET"
