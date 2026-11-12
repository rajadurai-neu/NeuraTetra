"""AI review step: interface + Claude implementation.

The engine depends only on the `Reviewer` protocol. The Claude client
(Azure AI Foundry or Anthropic API) is passed in by the caller.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict

from cnat_engine.domain import Finding, MeasureScore, NoteContext, Severity
from cnat_engine.rules.ruleset import QualityMeasure, RequiredElement

PROMPT_VERSION = "v2"
PROMPT_FILE = Path(__file__).parent / "prompts" / f"{PROMPT_VERSION}.md"


# ---- Fixed output structure the model must return (spec B3.12 / G17) ----

class AIFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rule_id: str
    element: str
    severity: Literal["critical", "warning", "info"]
    issue: str
    evidence_quote: str
    suggestion: str


class AIMeasure(BaseModel):
    model_config = ConfigDict(extra="forbid")
    measure: str
    score: int
    finding: str


class AIElement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    element_id: str
    present: bool
    evidence_quote: str


class AIReviewOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    findings: list[AIFinding]
    measure_scores: list[AIMeasure]
    elements: list[AIElement]


class ElementResult(BaseModel):
    element_id: str
    present: bool
    evidence: str | None = None


class ReviewResult(BaseModel):
    findings: list[Finding]
    measure_scores: list[MeasureScore]
    model: str
    prompt_version: str
    dropped_unsupported: int = 0  # findings removed because their quote wasn't in the note
    element_results: list[ElementResult] = []   # required elements checked (count toward compliance)


class Reviewer(Protocol):
    model: str
    prompt_version: str

    def review(self, note: NoteContext, measures: list[QualityMeasure],
               elements: list[RequiredElement] = ()) -> ReviewResult: ...


class AIRefusalError(RuntimeError):
    pass


def build_user_message(note: NoteContext, measures: list[QualityMeasure],
                       elements: list[RequiredElement] = ()) -> str:
    tp = note.treatment_plan
    context = {
        "note_type": note.note_type.value,
        "service_code": note.service_code,
        "modality": note.modality.value if note.modality else None,
        "treatment_plan_goals": tp.goals if tp else [],
        "treatment_plan_interventions": tp.interventions if tp else [],
    }
    measures_json = [m.model_dump(exclude={"applies_to"}) for m in measures]
    elements_json = [{"element_id": e.id, "name": e.name, "description": e.description} for e in elements]
    sections = "\n".join(f"[{k}]\n{v}" for k, v in note.sections.items()) if note.sections else ""
    return (
        "<context>\n" + json.dumps(context, indent=2) + "\n</context>\n\n"
        "<quality_measures>\n" + json.dumps(measures_json, indent=2) + "\n</quality_measures>\n\n"
        "<required_elements>\n" + json.dumps(elements_json, indent=2) + "\n</required_elements>\n\n"
        "<note>\n" + (sections or note.text) + "\n</note>\n\n"
        "Assess this draft note. Return findings, a score for every quality measure, and a present/missing "
        "result for every required element."
    )


def _norm(s: str) -> str:
    return " ".join(s.lower().split())


def to_review_result(out: AIReviewOutput, note: NoteContext, measures: list[QualityMeasure],
                     model: str, elements: list[RequiredElement] = ()) -> ReviewResult:
    """Validate model output: keep only grounded findings, known measures and known elements."""
    note_text = _norm(note.text + " " + " ".join(note.sections.values()))

    def grounded(quote: str) -> bool:
        return bool(quote) and _norm(quote) in note_text

    findings, dropped = [], 0
    for f in out.findings:
        quote = f.evidence_quote.strip()
        if not (quote.upper() == "ABSENT" or grounded(quote)):
            dropped += 1  # guard against invented content
            continue
        findings.append(Finding(
            rule_id=f.rule_id, element=f.element, source="Quality (AI review)",
            severity=Severity(f.severity), issue=f.issue, suggestion=f.suggestion,
            evidence=None if quote.upper() == "ABSENT" else quote, origin="ai",
        ))
    known = {m.id for m in measures}
    scores = [
        MeasureScore(measure=m.measure, score=max(0, min(3, m.score)), finding=m.finding)
        for m in out.measure_scores if m.measure in known
    ]

    # Required elements for this note type. "Present" must be backed by an exact quote; a present claim
    # with an invented quote is not counted either way. Elements the model didn't report aren't counted.
    answers = {e.element_id: e for e in out.elements}
    results: list[ElementResult] = []
    type_label = note.note_type.value.replace("_", " ")
    for el in elements:
        a = answers.get(el.id)
        if a is None:
            continue
        quote = a.evidence_quote.strip()
        if a.present:
            if grounded(quote):
                results.append(ElementResult(element_id=el.id, present=True, evidence=quote))
            else:
                dropped += 1
            continue
        results.append(ElementResult(element_id=el.id, present=False))
        findings.append(Finding(
            rule_id=el.rule_id, element=el.name, source="Note type: required elements",
            severity=el.severity,
            issue=f"Required for a {type_label}: {el.name} is missing.",
            suggestion=f"Document {el.description[0].lower() + el.description[1:]}",
            origin="ai",
        ))
    return ReviewResult(findings=findings, measure_scores=scores, model=model, prompt_version=PROMPT_VERSION,
                        dropped_unsupported=dropped, element_results=results)


class ClaudeReviewer:
    """Claude via the official Anthropic SDK. `client` is Anthropic() or AnthropicFoundry().

    refusal_fallback=True (Anthropic API only): if the model declines, the API re-runs the request on a
    fallback Claude model inside the same call, so notes about self-harm etc. still get reviewed.
    """

    FALLBACK_BETA = "server-side-fallback-2026-07-01"

    def __init__(self, client, model: str, effort: str = "medium", refusal_fallback: bool = False):
        self.client = client
        self.model = model
        self.effort = effort
        self.refusal_fallback = refusal_fallback
        self.prompt_version = PROMPT_VERSION
        self.system_prompt = PROMPT_FILE.read_text()

    def review(self, note: NoteContext, measures: list[QualityMeasure],
               elements: list[RequiredElement] = ()) -> ReviewResult:
        request = dict(
            model=self.model,
            max_tokens=16000,
            system=self.system_prompt,
            messages=[{"role": "user", "content": build_user_message(note, measures, elements)}],
            output_format=AIReviewOutput,
            output_config={"effort": self.effort},
        )
        if self.refusal_fallback:
            response = self.client.beta.messages.parse(**request, betas=[self.FALLBACK_BETA], fallbacks="default")
        else:
            response = self.client.messages.parse(**request)
        if response.stop_reason == "refusal":
            raise AIRefusalError("The model declined to review this note.")
        if response.stop_reason == "max_tokens" or response.parsed_output is None:
            raise RuntimeError(f"AI review incomplete (stop_reason={response.stop_reason}).")
        return to_review_result(response.parsed_output, note, measures, self.model, elements)


class NoAIReviewer:
    """Used when AI is switched off: rules-only assessment."""
    model = "none"
    prompt_version = "none"

    def review(self, note: NoteContext, measures: list[QualityMeasure],
               elements: list[RequiredElement] = ()) -> ReviewResult:
        return ReviewResult(findings=[], measure_scores=[], model=self.model, prompt_version=self.prompt_version)
