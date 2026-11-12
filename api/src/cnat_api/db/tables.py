"""Database tables. Portable SQLAlchemy types: SQLite locally, Azure SQL in production."""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    JSON, Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class NoteRow(Base):
    """One clinical note. The EHR stays the system of record; text is kept only as long as retention allows."""
    __tablename__ = "notes"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)        # e.g. "openemr:1234" or "manual:<uuid>"
    source: Mapped[str] = mapped_column(String(32))
    external_id: Mapped[str | None] = mapped_column(String(128))
    note_type: Mapped[str] = mapped_column(String(48))
    client_id: Mapped[str | None] = mapped_column(String(64))
    clinician_name: Mapped[str | None] = mapped_column(String(128))
    clinician_credentials: Mapped[str | None] = mapped_column(String(64))
    supervisor: Mapped[str | None] = mapped_column(String(128))
    program: Mapped[str | None] = mapped_column(String(128))
    clinic: Mapped[str | None] = mapped_column(String(128))
    date_of_service: Mapped[date | None] = mapped_column(Date)

    content_hash: Mapped[str] = mapped_column(String(64))                  # detect changed drafts
    text: Mapped[str | None] = mapped_column(Text)                         # cleared by retention job
    context: Mapped[dict] = mapped_column(JSON)                            # NoteContext minus text

    is_signed: Mapped[bool] = mapped_column(Boolean, default=False)
    signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    signed_with_open_issues: Mapped[bool | None] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    assessments: Mapped[list[AssessmentRow]] = relationship(back_populates="note", order_by="AssessmentRow.id")

    __table_args__ = (
        Index("ix_notes_clinician", "clinician_name"),
        Index("ix_notes_supervisor", "supervisor"),
        Index("ix_notes_program_dos", "program", "date_of_service"),
        UniqueConstraint("source", "external_id", name="uq_notes_source_external"),
    )


class AssessmentRow(Base):
    __tablename__ = "assessments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    note_id: Mapped[str] = mapped_column(ForeignKey("notes.id"))
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    trigger: Mapped[str] = mapped_column(String(32))                       # on_demand | auto | schedule | retro
    status: Mapped[str] = mapped_column(String(32))
    compliance_score: Mapped[float] = mapped_column(Float)
    quality_score: Mapped[float | None] = mapped_column(Float)
    overall_score: Mapped[float] = mapped_column(Float)
    rules_checked: Mapped[int] = mapped_column(Integer)
    rules_passed: Mapped[int] = mapped_column(Integer)
    critical_count: Mapped[int] = mapped_column(Integer)
    warning_count: Mapped[int] = mapped_column(Integer)
    ruleset_version: Mapped[str] = mapped_column(String(64))
    prompt_version: Mapped[str | None] = mapped_column(String(32))
    model: Mapped[str | None] = mapped_column(String(64))
    input_hash: Mapped[str] = mapped_column(String(64))
    content_hash: Mapped[str] = mapped_column(String(64))
    assessed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    first_for_note: Mapped[bool] = mapped_column(Boolean, default=False)   # "met standards at first assessment" report

    note: Mapped[NoteRow] = relationship(back_populates="assessments")
    findings: Mapped[list[FindingRow]] = relationship(back_populates="assessment", cascade="all, delete-orphan")
    measures: Mapped[list[MeasureRow]] = relationship(back_populates="assessment", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_assess_note_current", "note_id", "is_current"),
        Index("ix_assess_date", "assessed_at"),
    )


class FindingRow(Base):
    __tablename__ = "findings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    assessment_id: Mapped[int] = mapped_column(ForeignKey("assessments.id"))
    rule_id: Mapped[str] = mapped_column(String(64))
    element: Mapped[str] = mapped_column(String(128))
    source: Mapped[str] = mapped_column(String(64))
    severity: Mapped[str] = mapped_column(String(16))
    issue: Mapped[str] = mapped_column(Text)
    suggestion: Mapped[str] = mapped_column(Text)
    evidence: Mapped[str | None] = mapped_column(Text)
    origin: Mapped[str] = mapped_column(String(8))
    not_applicable_reason: Mapped[str | None] = mapped_column(Text)

    assessment: Mapped[AssessmentRow] = relationship(back_populates="findings")

    __table_args__ = (Index("ix_findings_rule", "rule_id"),)


class MeasureRow(Base):
    __tablename__ = "measure_scores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    assessment_id: Mapped[int] = mapped_column(ForeignKey("assessments.id"))
    measure: Mapped[str] = mapped_column(String(64))
    score: Mapped[int] = mapped_column(Integer)
    max_score: Mapped[int] = mapped_column(Integer)
    finding: Mapped[str] = mapped_column(Text)

    assessment: Mapped[AssessmentRow] = relationship(back_populates="measures")


class NotApplicableRow(Base):
    """A clinician's 'not applicable' mark on a rule for one note, with the reason (visible to supervisors/QA)."""
    __tablename__ = "not_applicable"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    note_id: Mapped[str] = mapped_column(ForeignKey("notes.id"))
    rule_id: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(Text)
    marked_by: Mapped[str] = mapped_column(String(128))
    marked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (UniqueConstraint("note_id", "rule_id", name="uq_na_note_rule"),)


class NoteTypeOverrideRow(Base):
    """A user's correction of a note's type (spec A2.8). Applied on every assessment, so EHR syncs keep it."""
    __tablename__ = "note_type_overrides"

    note_id: Mapped[str] = mapped_column(ForeignKey("notes.id"), primary_key=True)
    note_type: Mapped[str] = mapped_column(String(48))
    reason: Mapped[str] = mapped_column(Text)
    set_by: Mapped[str] = mapped_column(String(128))
    set_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    detected_type: Mapped[str | None] = mapped_column(String(48))        # what the EHR/default said
    detected_source: Mapped[str | None] = mapped_column(String(256))


class AIReviewCacheRow(Base):
    """Stored AI review per input hash, so the same note + versions give the same result."""
    __tablename__ = "ai_review_cache"

    input_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    result: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AuditRow(Base):
    """Append-only audit log. The app never updates or deletes these rows."""
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    actor: Mapped[str] = mapped_column(String(128))          # user or "system"
    action: Mapped[str] = mapped_column(String(64))          # note_retrieved | assessment_run | results_viewed | ...
    record_type: Mapped[str] = mapped_column(String(32))
    record_id: Mapped[str] = mapped_column(String(128))
    details: Mapped[dict | None] = mapped_column(JSON)

    __table_args__ = (Index("ix_audit_record", "record_type", "record_id"), Index("ix_audit_at", "at"))
