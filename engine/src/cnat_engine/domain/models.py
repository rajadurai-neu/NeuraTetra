"""Core domain types. Plain data, no I/O, no framework code."""
from __future__ import annotations

from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, Field


class NoteType(str, Enum):
    PROGRESS_INDIVIDUAL = "progress_individual"
    PROGRESS_GROUP = "progress_group"
    PROGRESS_FAMILY = "progress_family"
    PSYCHIATRIC_EVALUATION = "psychiatric_evaluation"
    PSYCHOSOCIAL_ASSESSMENT = "psychosocial_assessment"
    TREATMENT_PLAN = "treatment_plan"
    TREATMENT_PLAN_REVIEW = "treatment_plan_review"
    CRISIS_SAFETY = "crisis_safety"
    MEDICATION_MANAGEMENT = "medication_management"
    COLLATERAL_CONTACT = "collateral_contact"
    DISCHARGE_SUMMARY = "discharge_summary"


class Modality(str, Enum):
    IN_PERSON = "in_person"
    VIDEO = "video"
    AUDIO_ONLY = "audio_only"


class Severity(str, Enum):
    CRITICAL = "critical"  # must fix before signing
    WARNING = "warning"    # should fix
    INFO = "info"


class Status(str, Enum):
    MEETS = "meets_standards"
    NEEDS_ATTENTION = "needs_attention"
    DOES_NOT_MEET = "does_not_meet_standards"


class TreatmentPlan(BaseModel):
    start_date: date | None = None
    end_date: date | None = None
    review_due: date | None = None
    goals: list[str] = Field(default_factory=list)
    interventions: list[str] = Field(default_factory=list)


class NoteContext(BaseModel):
    """Everything the engine needs about one note. Built by an intake adapter."""
    note_id: str
    source: str = "manual"                 # "openemr" | "manual"
    note_type: NoteType
    text: str
    sections: dict[str, str] = Field(default_factory=dict)  # S/O/A/P if the EHR stores them separately

    client_id: str | None = None
    date_of_service: date | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    service_code: str | None = None
    modality: Modality | None = None
    participants: list[str] = Field(default_factory=list)
    client_location: str | None = None     # telehealth
    telehealth_consent: bool | None = None

    clinician_name: str | None = None
    clinician_credentials: str | None = None
    supervisor: str | None = None
    program: str | None = None
    clinic: str | None = None

    treatment_plan: TreatmentPlan | None = None
    prior_note_texts: list[str] = Field(default_factory=list)  # same client, for clone check
    is_signed: bool = False

    @property
    def duration_minutes(self) -> int | None:
        if self.start_time and self.end_time:
            return int((self.end_time - self.start_time).total_seconds() // 60)
        return None


class Finding(BaseModel):
    rule_id: str
    element: str                  # section/element affected
    source: str                   # Part 599 | OMH | SOAP | Agency | Quality
    severity: Severity
    issue: str                    # what's missing or wrong
    suggestion: str               # how to fix (never clinical content)
    evidence: str | None = None   # quote from the note, if any
    origin: str = "rule"          # "rule" | "ai"
    not_applicable_reason: str | None = None


class MeasureScore(BaseModel):
    measure: str
    score: int = Field(ge=0, le=3)
    max_score: int = 3
    finding: str


class Assessment(BaseModel):
    note_id: str
    status: Status
    compliance_score: float       # % of applicable required elements met
    quality_score: float | None   # % from AI quality measures
    overall_score: float
    findings: list[Finding]
    measure_scores: list[MeasureScore] = Field(default_factory=list)
    rules_checked: int
    rules_passed: int
    ruleset_version: str
    prompt_version: str | None
    model: str | None
    input_hash: str
    assessed_at: datetime
