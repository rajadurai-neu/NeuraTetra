"""Validation set: synthetic notes with known expected results (spec B3.15 / G21)."""
import json
from datetime import datetime
from pathlib import Path

import pytest

from cnat_engine.domain import NoteContext, Severity, Status
from cnat_engine.ai.reviewer import AIFinding, AIMeasure, AIReviewOutput, NoAIReviewer, to_review_result
from cnat_engine.pipeline import InMemoryReviewCache, assess
from cnat_engine.rules.checks import split_soap
from cnat_engine.rules.ruleset import load_ruleset

SAMPLES = Path(__file__).resolve().parents[2] / "rulesets" / "samples"
AS_OF = datetime(2026, 11, 10, 12, 0)
RULESET = load_ruleset(SAMPLES.parent)


def load(name):
    d = json.loads((SAMPLES / f"{name}.json").read_text())
    return NoteContext(**d["note"]), d["expected"]


@pytest.mark.parametrize("name", sorted(p.stem for p in SAMPLES.glob("*.json")))
def test_rules_match_expected(name):
    note, expected = load(name)
    a = assess(note, RULESET, NoAIReviewer(), as_of=AS_OF)
    got = sorted(f.rule_id for f in a.findings)
    assert got == sorted(expected["rule_ids"])
    assert a.status.value == expected["status_rules"]


def test_ruleset_loads_and_has_versions():
    assert RULESET.version and RULESET.quality_version
    assert len(RULESET.measures) == 8


def test_soap_split():
    note, _ = load("good_progress")
    assert set(split_soap(note.text)) == {"subjective", "objective", "assessment", "plan"}


def test_negated_risk_language_does_not_trigger():
    note, _ = load("good_progress")  # contains "Denies SI/HI."
    a = assess(note, RULESET, NoAIReviewer(), as_of=AS_OF)
    assert "OMH-RISK-SAFETY" not in {f.rule_id for f in a.findings}


def test_timeliness_past_due():
    note, _ = load("good_progress")
    a = assess(note, RULESET, NoAIReviewer(), as_of=datetime(2026, 11, 13, 12, 0))
    f = next(f for f in a.findings if f.rule_id == "AGY-TIMELINESS")
    assert "Past due" in f.issue


def test_na_override_excluded_from_score_but_visible():
    note, _ = load("telehealth_missing")
    a = assess(note, RULESET, NoAIReviewer(), as_of=AS_OF,
               na_overrides={"OMH-TELE-CONSENT": "Consent on file in intake packet"})
    consent = next(f for f in a.findings if f.rule_id == "OMH-TELE-CONSENT")
    assert consent.not_applicable_reason
    assert a.findings[-1].rule_id == "OMH-TELE-CONSENT"  # N/A items sorted last


class FakeReviewer:
    model = "fake-model"
    prompt_version = "v1"

    def __init__(self, output: AIReviewOutput):
        self.output, self.calls = output, 0

    def review(self, note, measures):
        self.calls += 1
        return to_review_result(self.output, note, measures, self.model)


def fake_output():
    return AIReviewOutput(
        findings=[
            AIFinding(rule_id="AI-measurable_progress", element="Assessment", severity="warning",
                      issue="Progress described vaguely.", evidence_quote="Client doing fine.",
                      suggestion="Describe progress in observable terms."),
            AIFinding(rule_id="AI-invented", element="Subjective", severity="critical",
                      issue="Invented", evidence_quote="client reported hearing voices",  # not in note
                      suggestion="x"),
            AIFinding(rule_id="AI-clear_plan", element="Plan", severity="warning",
                      issue="No next session.", evidence_quote="ABSENT", suggestion="State the next session date."),
        ],
        measure_scores=[AIMeasure(measure="clear_plan", score=1, finding="Vague"),
                        AIMeasure(measure="measurable_progress", score=0, finding="None"),
                        AIMeasure(measure="not_a_measure", score=3, finding="ignored")],
    )


def test_ai_findings_must_be_grounded_in_note():
    note, _ = load("poor_progress")
    rev = FakeReviewer(fake_output())
    a = assess(note, RULESET, rev, as_of=AS_OF)
    ai_ids = {f.rule_id for f in a.findings if f.origin == "ai"}
    assert ai_ids == {"AI-measurable_progress", "AI-clear_plan"}  # invented quote dropped
    assert {m.measure for m in a.measure_scores} == {"clear_plan", "measurable_progress"}
    assert a.quality_score == round(100 * 1 / 6, 1)
    assert a.findings[0].severity == Severity.CRITICAL


def test_same_note_same_versions_reuses_result():
    note, _ = load("poor_progress")
    rev, cache = FakeReviewer(fake_output()), InMemoryReviewCache()
    a1 = assess(note, RULESET, rev, as_of=AS_OF, cache=cache)
    a2 = assess(note, RULESET, rev, as_of=AS_OF, cache=cache)
    assert rev.calls == 1
    assert a1.model_dump(exclude={"assessed_at"}) == a2.model_dump(exclude={"assessed_at"})


def test_changed_note_gets_new_hash():
    note, _ = load("poor_progress")
    rev, cache = FakeReviewer(fake_output()), InMemoryReviewCache()
    a1 = assess(note, RULESET, rev, as_of=AS_OF, cache=cache)
    a2 = assess(note.model_copy(update={"text": note.text + " Edited."}), RULESET, rev, as_of=AS_OF, cache=cache)
    assert a1.input_hash != a2.input_hash and rev.calls == 2


def test_status_meets_when_clean():
    note, _ = load("good_progress")
    assert assess(note, RULESET, NoAIReviewer(), as_of=AS_OF).status == Status.MEETS


def test_unavailable_fields_skip_rules_instead_of_failing():
    note, _ = load("poor_progress")
    note = note.model_copy(update={"unavailable_fields": ["start_time", "participants", "clinician_credentials"]})
    a = assess(note, RULESET, NoAIReviewer(), as_of=AS_OF)
    ids = {f.rule_id for f in a.findings}
    assert not ids & {"P599-TIMES", "P599-PARTICIPANTS", "P599-CREDENTIALS"}
    assert "OMH-RISK-SAFETY" in ids  # everything else still checked
