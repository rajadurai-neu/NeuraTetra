"""The assessment pipeline: rules -> AI review -> scoring.

Same pipeline for every intake source (OpenEMR drafts, pasted/uploaded notes).
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Protocol

from cnat_engine.domain import Assessment, Finding, NoteContext, Severity
from cnat_engine import scoring
from cnat_engine.ai.reviewer import Reviewer, ReviewResult
from cnat_engine.rules.ruleset import RuleSet
from cnat_engine.rules.runner import run_rules

SEVERITY_ORDER = {Severity.CRITICAL: 0, Severity.WARNING: 1, Severity.INFO: 2}


class ReviewCache(Protocol):
    def get(self, key: str) -> ReviewResult | None: ...
    def put(self, key: str, value: ReviewResult) -> None: ...


class InMemoryReviewCache:
    def __init__(self):
        self._d: dict[str, ReviewResult] = {}

    def get(self, key):
        return self._d.get(key)

    def put(self, key, value):
        self._d[key] = value


def ai_input_hash(note: NoteContext, ruleset: RuleSet, reviewer: Reviewer) -> str:
    """Everything the AI sees + versions. Same hash -> reuse stored result (spec B3.16 / G18)."""
    payload = {
        "text": note.text,
        "sections": note.sections,
        "note_type": note.note_type.value,
        "service_code": note.service_code,
        "modality": note.modality.value if note.modality else None,
        "treatment_plan": note.treatment_plan.model_dump(mode="json") if note.treatment_plan else None,
        "quality_version": ruleset.quality_version,
        "prompt_version": reviewer.prompt_version,
        "model": reviewer.model,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def assess(note: NoteContext, ruleset: RuleSet, reviewer: Reviewer, *,
           as_of: datetime | None = None, na_overrides: dict[str, str] | None = None,
           cache: ReviewCache | None = None) -> Assessment:
    as_of = as_of or datetime.now(timezone.utc)
    na_overrides = na_overrides or {}

    # 1. Deterministic rules (cheap; always re-run so timeliness is current)
    rules = run_rules(note, ruleset, as_of, na_overrides)

    # 2. AI review (cached by content + versions)
    key = ai_input_hash(note, ruleset, reviewer)
    review = cache.get(key) if cache else None
    if review is None:
        review = reviewer.review(note, ruleset.measures)
        if cache:
            cache.put(key, review)

    ai_findings: list[Finding] = []
    for f in review.findings:
        if f.rule_id in na_overrides:
            f = f.model_copy(update={"not_applicable_reason": na_overrides[f.rule_id]})
        ai_findings.append(f)

    # 3. Score
    findings = sorted(rules.findings + ai_findings,
                      key=lambda f: (f.not_applicable_reason is not None, SEVERITY_ORDER[f.severity], f.rule_id))
    compliance = scoring.compliance_score(rules.checked, rules.passed)
    quality = scoring.quality_score(review.measure_scores)
    return Assessment(
        note_id=note.note_id,
        status=scoring.status_for(findings),
        compliance_score=compliance,
        quality_score=quality,
        overall_score=scoring.overall_score(compliance, quality, ruleset.compliance_weight, ruleset.quality_weight),
        findings=findings,
        measure_scores=review.measure_scores,
        rules_checked=rules.checked,
        rules_passed=rules.passed,
        ruleset_version=ruleset.full_version,
        prompt_version=review.prompt_version,
        model=review.model,
        input_hash=key,
        assessed_at=as_of,
    )
