"""Request/response shapes for the HTTP API."""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field

from cnat_engine.domain import Assessment, NoteContext, NoteType, Severity
from cnat_api.timefmt import fmt_et


class NoteIn(NoteContext):
    """A note sent for assessment. note_id is generated when omitted (pasted/typed notes)."""
    note_id: str | None = None  # type: ignore[assignment]
    note_type: NoteType = NoteType.PROGRESS_INDIVIDUAL
    source: str = "manual"


class FixItem(BaseModel):
    severity: Severity
    element: str
    issue: str
    fix: str


class ChangesOut(BaseModel):
    resolved: list[str] = []
    new: list[str] = []
    persisting: list[str] = []


class AssessmentOut(BaseModel):
    assessment_id: int | None = None
    reused: bool = False
    assessed_at_display: str
    fix_first: list[FixItem]               # what to fix, at a glance (B8.36)
    changes: ChangesOut | None = None
    assessment: Assessment

    @classmethod
    def build(cls, a: Assessment, assessment_id: int | None = None, reused: bool = False,
              changes=None) -> AssessmentOut:
        fix = [FixItem(severity=f.severity, element=f.element, issue=f.issue, fix=f.suggestion)
               for f in a.findings if not f.not_applicable_reason and f.severity != Severity.INFO]
        return cls(assessment_id=assessment_id, reused=reused, assessed_at_display=fmt_et(a.assessed_at),
                   fix_first=fix, changes=ChangesOut(**vars(changes)) if changes else None, assessment=a)


class NotApplicableIn(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)


class QueueItem(BaseModel):
    note_id: str
    note_type: str
    client_id: str | None
    clinician: str | None
    supervisor: str | None
    program: str | None
    clinic: str | None
    date_of_service: date | None
    is_signed: bool
    signed_with_open_issues: bool | None
    status: str | None
    overall_score: float | None
    critical: int | None
    warning: int | None
    assessed_at: datetime | None
    assessed_at_display: str = ""


class HistoryItem(BaseModel):
    assessment_id: int
    assessed_at: datetime
    assessed_at_display: str
    trigger: str
    status: str
    overall_score: float
    critical: int
    warning: int
    resolved: list[str]
    new: list[str]
    is_current: bool
    ruleset_version: str
    model: str | None


class BatchResult(BaseModel):
    note_id: str
    ok: bool
    status: str | None = None
    overall_score: float | None = None
    error: str | None = None


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)
