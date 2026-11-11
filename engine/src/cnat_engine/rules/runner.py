"""Run deterministic rules against a note."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from cnat_engine.domain import Finding, NoteContext
from cnat_engine.rules.checks import REGISTRY
from cnat_engine.rules.ruleset import RuleSet


@dataclass
class RuleRunResult:
    findings: list[Finding]
    checked: int   # applicable rules
    passed: int


def run_rules(note: NoteContext, ruleset: RuleSet, as_of: datetime,
              na_overrides: dict[str, str] | None = None) -> RuleRunResult:
    """na_overrides: {rule_id: reason} set by a clinician ("not applicable")."""
    na_overrides = na_overrides or {}
    findings: list[Finding] = []
    checked = passed = 0

    for rule in ruleset.rules_for(note.note_type, note.program):
        fn = REGISTRY.get(rule.check)
        if fn is None:
            raise KeyError(f"Rule {rule.id} uses unknown check '{rule.check}'")
        result = fn(note, rule.params, as_of)
        if not result.applicable:
            continue
        if rule.id in na_overrides:
            # Kept visible for supervisors/QA but excluded from the score.
            findings.append(Finding(
                rule_id=rule.id, element=rule.element, source=rule.source, severity=rule.severity,
                issue=rule.issue, suggestion=rule.suggestion, not_applicable_reason=na_overrides[rule.id],
            ))
            continue
        checked += 1
        if result.passed:
            passed += 1
            continue
        issue = f"{rule.issue} {result.detail}" if result.detail else rule.issue
        findings.append(Finding(
            rule_id=rule.id, element=rule.element, source=rule.source, severity=rule.severity,
            issue=issue, suggestion=rule.suggestion, evidence=result.evidence,
        ))
    return RuleRunResult(findings, checked, passed)
