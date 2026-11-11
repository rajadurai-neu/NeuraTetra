"""Turn findings and measure scores into status and scores."""
from __future__ import annotations

from cnat_engine.domain import Finding, MeasureScore, Severity, Status


def status_for(findings: list[Finding]) -> Status:
    active = [f for f in findings if not f.not_applicable_reason]
    if any(f.severity == Severity.CRITICAL for f in active):
        return Status.DOES_NOT_MEET
    if any(f.severity == Severity.WARNING for f in active):
        return Status.NEEDS_ATTENTION
    return Status.MEETS


def compliance_score(checked: int, passed: int) -> float:
    return round(100.0 * passed / checked, 1) if checked else 100.0


def quality_score(scores: list[MeasureScore]) -> float | None:
    if not scores:
        return None
    return round(100.0 * sum(s.score for s in scores) / sum(s.max_score for s in scores), 1)


def overall_score(compliance: float, quality: float | None, cw: float, qw: float) -> float:
    if quality is None:
        return compliance
    return round((compliance * cw + quality * qw) / (cw + qw), 1)
