"""Data access: the only module that knows about table rows. Services use these functions."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session, selectinload

from cnat_api.db.tables import (
    AIReviewCacheRow, AssessmentRow, AuditRow, FindingRow, MeasureRow, NotApplicableRow, NoteRow,
    NoteTypeOverrideRow,
)
from cnat_engine.domain import Assessment, Finding, MeasureScore, NoteContext, Severity, Status
from cnat_engine.ai.reviewer import ReviewResult


def note_content_hash(note: NoteContext) -> str:
    """Fingerprint of everything that affects the result (prior notes excluded: they change on their own)."""
    data = note.model_dump(mode="json", exclude={"prior_note_texts"})
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


# ---------------- Notes ----------------

def upsert_note(s: Session, note: NoteContext, now: datetime, keep_text: bool = True) -> tuple[NoteRow, bool]:
    """Insert or update a note. Returns (row, changed_since_last_save)."""
    h = note_content_hash(note)
    row = s.get(NoteRow, note.note_id)
    fields = dict(
        source=note.source, note_type=note.note_type.value, client_id=note.client_id,
        clinician_name=note.clinician_name, clinician_credentials=note.clinician_credentials,
        supervisor=note.supervisor, program=note.program, clinic=note.clinic,
        date_of_service=note.date_of_service, is_signed=note.is_signed,
        text=note.text if keep_text else None,
        context=note.model_dump(mode="json", exclude={"text", "prior_note_texts"}),
        content_hash=h, updated_at=now,
    )
    if row is None:
        row = NoteRow(id=note.note_id, external_id=note.note_id.split(":", 1)[-1], created_at=now, **fields)
        s.add(row)
        return row, True
    changed = row.content_hash != h
    for k, v in fields.items():
        setattr(row, k, v)
    return row, changed


def get_note(s: Session, note_id: str) -> NoteRow | None:
    return s.get(NoteRow, note_id)


# ---------------- Not applicable ----------------

def na_overrides(s: Session, note_id: str) -> dict[str, str]:
    rows = s.scalars(select(NotApplicableRow).where(NotApplicableRow.note_id == note_id))
    return {r.rule_id: r.reason for r in rows}


def set_not_applicable(s: Session, note_id: str, rule_id: str, reason: str, user: str, now: datetime) -> None:
    row = s.scalar(select(NotApplicableRow).where(
        NotApplicableRow.note_id == note_id, NotApplicableRow.rule_id == rule_id))
    if row:
        row.reason, row.marked_by, row.marked_at = reason, user, now
    else:
        s.add(NotApplicableRow(note_id=note_id, rule_id=rule_id, reason=reason, marked_by=user, marked_at=now))


def clear_not_applicable(s: Session, note_id: str, rule_id: str) -> None:
    row = s.scalar(select(NotApplicableRow).where(
        NotApplicableRow.note_id == note_id, NotApplicableRow.rule_id == rule_id))
    if row:
        s.delete(row)


# ---------------- Note type corrections ----------------

def note_type_override(s: Session, note_id: str) -> NoteTypeOverrideRow | None:
    return s.get(NoteTypeOverrideRow, note_id)


def set_note_type_override(s: Session, note_id: str, note_type: str, reason: str, user: str, now: datetime,
                           detected_type: str | None, detected_source: str | None) -> None:
    row = s.get(NoteTypeOverrideRow, note_id)
    if row:   # keep what the EHR originally said
        row.note_type, row.reason, row.set_by, row.set_at = note_type, reason, user, now
    else:
        s.add(NoteTypeOverrideRow(note_id=note_id, note_type=note_type, reason=reason, set_by=user, set_at=now,
                                  detected_type=detected_type, detected_source=detected_source))


def clear_note_type_override(s: Session, note_id: str) -> NoteTypeOverrideRow | None:
    row = s.get(NoteTypeOverrideRow, note_id)
    if row:
        s.delete(row)
    return row


# ---------------- Assessments ----------------

def save_assessment(s: Session, a: Assessment, trigger: str, content_hash: str) -> AssessmentRow:
    """Store a new assessment as the current one; earlier ones stay as history."""
    had_any = s.scalar(select(AssessmentRow.id).where(AssessmentRow.note_id == a.note_id).limit(1)) is not None
    s.execute(update(AssessmentRow).where(AssessmentRow.note_id == a.note_id, AssessmentRow.is_current.is_(True))
              .values(is_current=False))
    active = [f for f in a.findings if not f.not_applicable_reason]
    row = AssessmentRow(
        note_id=a.note_id, is_current=True, trigger=trigger, status=a.status.value,
        compliance_score=a.compliance_score, quality_score=a.quality_score, overall_score=a.overall_score,
        rules_checked=a.rules_checked, rules_passed=a.rules_passed,
        critical_count=sum(f.severity == Severity.CRITICAL for f in active),
        warning_count=sum(f.severity == Severity.WARNING for f in active),
        ruleset_version=a.ruleset_version, prompt_version=a.prompt_version, model=a.model,
        input_hash=a.input_hash, content_hash=content_hash, assessed_at=a.assessed_at,
        first_for_note=not had_any,
        findings=[FindingRow(**f.model_dump(mode="json")) for f in a.findings],
        measures=[MeasureRow(**m.model_dump()) for m in a.measure_scores],
    )
    s.add(row)
    s.flush()
    return row


def _load(s: Session):
    return select(AssessmentRow).options(selectinload(AssessmentRow.findings), selectinload(AssessmentRow.measures))


def current_assessment(s: Session, note_id: str) -> AssessmentRow | None:
    return s.scalar(_load(s).where(AssessmentRow.note_id == note_id, AssessmentRow.is_current.is_(True)))


def assessment_history(s: Session, note_id: str) -> list[AssessmentRow]:
    return list(s.scalars(_load(s).where(AssessmentRow.note_id == note_id).order_by(AssessmentRow.id)))


def to_domain(row: AssessmentRow) -> Assessment:
    return Assessment(
        note_id=row.note_id, status=Status(row.status), compliance_score=row.compliance_score,
        quality_score=row.quality_score, overall_score=row.overall_score,
        findings=[Finding(rule_id=f.rule_id, element=f.element, source=f.source, severity=Severity(f.severity),
                          issue=f.issue, suggestion=f.suggestion, evidence=f.evidence, origin=f.origin,
                          not_applicable_reason=f.not_applicable_reason) for f in row.findings],
        measure_scores=[MeasureScore(measure=m.measure, score=m.score, max_score=m.max_score, finding=m.finding)
                        for m in row.measures],
        rules_checked=row.rules_checked, rules_passed=row.rules_passed, ruleset_version=row.ruleset_version,
        prompt_version=row.prompt_version, model=row.model, input_hash=row.input_hash, assessed_at=row.assessed_at,
    )


# ---------------- AI review cache ----------------

class DBReviewCache:
    """ReviewCache backed by the database (implements cnat_engine.pipeline.ReviewCache)."""

    def __init__(self, s: Session, now: datetime):
        self.s, self.now = s, now

    def get(self, key: str) -> ReviewResult | None:
        row = self.s.get(AIReviewCacheRow, key)
        return ReviewResult.model_validate(row.result) if row else None

    def put(self, key: str, value: ReviewResult) -> None:
        self.s.merge(AIReviewCacheRow(input_hash=key, result=value.model_dump(mode="json"), created_at=self.now))


# ---------------- Audit (append-only) ----------------

def audit(s: Session, actor: str, action: str, record_type: str, record_id: str, now: datetime,
          details: dict | None = None) -> None:
    s.add(AuditRow(at=now, actor=actor, action=action, record_type=record_type,
                   record_id=record_id, details=details))


def audit_trail(s: Session, record_type: str, record_id: str) -> list[AuditRow]:
    return list(s.scalars(select(AuditRow).where(AuditRow.record_type == record_type,
                                                 AuditRow.record_id == record_id).order_by(AuditRow.id)))


# ---------------- Queues / filters (supervisor queue, QA filters) ----------------

def list_notes(s: Session, *, clinician: str | None = None, supervisor: str | None = None,
               program: str | None = None, note_type: str | None = None, status: str | None = None,
               date_from=None, date_to=None, signed: bool | None = None,
               limit: int = 200, offset: int = 0) -> list[tuple[NoteRow, AssessmentRow | None]]:
    """Notes with their current assessment (if any), newest date of service first."""
    q = (select(NoteRow, AssessmentRow)
         .outerjoin(AssessmentRow, (AssessmentRow.note_id == NoteRow.id) & AssessmentRow.is_current.is_(True)))
    if clinician:
        q = q.where(NoteRow.clinician_name == clinician)
    if supervisor:
        q = q.where(NoteRow.supervisor == supervisor)
    if program:
        q = q.where(NoteRow.program == program)
    if note_type:
        q = q.where(NoteRow.note_type == note_type)
    if status:
        q = q.where(AssessmentRow.status == status)
    if date_from:
        q = q.where(NoteRow.date_of_service >= date_from)
    if date_to:
        q = q.where(NoteRow.date_of_service <= date_to)
    if signed is not None:
        q = q.where(NoteRow.is_signed.is_(signed))
    q = q.order_by(NoteRow.date_of_service.desc(), NoteRow.id).limit(limit).offset(offset)
    return [(n, a) for n, a in s.execute(q).all()]


# ---------------- Reports ----------------

def report_rows(s: Session, *, clinician=None, supervisor=None, program=None, note_type=None,
                date_from=None, date_to=None):
    """Notes in scope, each with its full assessment history (for dashboards)."""
    q = select(NoteRow).options(
        selectinload(NoteRow.assessments).selectinload(AssessmentRow.findings))
    if clinician:
        q = q.where(NoteRow.clinician_name == clinician)
    if supervisor:
        q = q.where(NoteRow.supervisor == supervisor)
    if program:
        q = q.where(NoteRow.program == program)
    if note_type:
        q = q.where(NoteRow.note_type == note_type)
    if date_from:
        q = q.where(NoteRow.date_of_service >= date_from)
    if date_to:
        q = q.where(NoteRow.date_of_service <= date_to)
    return list(s.scalars(q))
