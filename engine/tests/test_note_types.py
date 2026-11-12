"""Per-note-type required elements and quality measures (spec A2.7)."""
from datetime import datetime, timezone
from pathlib import Path

import pytest

from cnat_engine.ai.reviewer import AIElement, AIReviewOutput, NoAIReviewer, build_user_message, to_review_result
from cnat_engine.domain import NoteContext, NoteType
from cnat_engine.pipeline import ai_input_hash, assess
from cnat_engine.rules.ruleset import load_ruleset

RULES = load_ruleset(Path(__file__).resolve().parents[2] / "rulesets")
AS_OF = datetime(2026, 11, 12, 15, 0, tzinfo=timezone.utc)
DISCHARGE = ("Client is being discharged because treatment goals were met. Over 12 weeks of CBT the client "
             "learned coping skills. Client reports no panic attacks in the last month.")


def note(text=DISCHARGE, note_type=NoteType.DISCHARGE_SUMMARY):
    return NoteContext(note_id="n", note_type=note_type, text=text,
                       unavailable_fields=["start_time", "clinician_name", "clinician_credentials", "modality",
                                           "service_code", "participants", "treatment_plan"])


class ElementReviewer:
    model, prompt_version = "fake", "v2"

    def __init__(self, answers):
        self.answers, self.seen = answers, None

    def review(self, n, measures, elements=()):
        self.seen = (measures, elements)
        out = AIReviewOutput(findings=[], measure_scores=[], elements=[AIElement(**a) for a in self.answers])
        return to_review_result(out, n, measures, self.model, elements)


def test_every_note_type_has_elements_and_measures():
    for t in NoteType:
        assert RULES.elements_for(t), t
        assert RULES.measures_for(t), t
    assert "soap_content" not in {m.id for m in RULES.measures_for(NoteType.DISCHARGE_SUMMARY)}
    assert "soap_content" in {m.id for m in RULES.measures_for(NoteType.PROGRESS_INDIVIDUAL)}


def test_missing_elements_become_findings_and_count_in_compliance():
    rev = ElementReviewer([
        {"element_id": "reason_for_discharge", "present": True,
         "evidence_quote": "discharged because treatment goals were met"},
        {"element_id": "treatment_summary", "present": True, "evidence_quote": "Over 12 weeks of CBT"},
        {"element_id": "referrals_aftercare", "present": False, "evidence_quote": "ABSENT"},
        {"element_id": "status_at_discharge", "present": True, "evidence_quote": "client is stable and happy"},  # invented
    ])                                                  # progress_on_goals not reported -> not counted
    a = assess(note(), RULES, rev, as_of=AS_OF)
    measures, elements = rev.seen
    assert [e.id for e in elements] == [e.id for e in RULES.elements_for(NoteType.DISCHARGE_SUMMARY)]
    assert {m.id for m in measures} == {m.id for m in RULES.measures_for(NoteType.DISCHARGE_SUMMARY)}

    missing = [f for f in a.findings if f.rule_id.startswith("EL-")]
    assert [f.rule_id for f in missing] == ["EL-referrals_aftercare"]
    assert missing[0].severity.value == "critical" and "discharge summary" in missing[0].issue
    assert a.status.value == "does_not_meet_standards"
    # rules checked + 3 grounded element answers (2 present, 1 missing); the invented one isn't counted
    rules_only = assess(note(), RULES, NoAIReviewer(), as_of=AS_OF)
    assert a.rules_checked == rules_only.rules_checked + 3
    assert a.rules_passed == rules_only.rules_passed + 2


def test_element_ids_and_types_change_the_ai_cache_key():
    rev = NoAIReviewer()
    h_dc = ai_input_hash(note(), RULES, rev)
    h_pe = ai_input_hash(note(note_type=NoteType.PSYCHIATRIC_EVALUATION), RULES, rev)
    assert h_dc != h_pe


def test_prompt_lists_required_elements():
    msg = build_user_message(note(), RULES.measures_for(NoteType.DISCHARGE_SUMMARY),
                             RULES.elements_for(NoteType.DISCHARGE_SUMMARY))
    assert "<required_elements>" in msg and "referrals_aftercare" in msg and "soap_content" not in msg


def test_unknown_note_type_in_yaml_fails_loudly(tmp_path):
    import shutil
    for f in (Path(__file__).resolve().parents[2] / "rulesets").glob("*.yaml"):
        shutil.copy(f, tmp_path)
    (tmp_path / "note_types.yaml").write_text('version: "x"\ntypes:\n  not_a_type: []\n')
    with pytest.raises(ValueError):
        load_ruleset(tmp_path)
