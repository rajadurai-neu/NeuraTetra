"""Deterministic check functions, referenced by name from the YAML rules.

Each check returns a CheckResult:
  PASS            -> requirement met
  NA              -> rule doesn't apply to this note (not counted in the score)
  fail("detail")  -> requirement not met; detail is appended to the rule's issue text
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

from cnat.domain import Modality, NoteContext


@dataclass(frozen=True)
class CheckResult:
    passed: bool
    applicable: bool = True
    detail: str | None = None
    evidence: str | None = None


PASS = CheckResult(True)
NA = CheckResult(True, applicable=False)


def fail(detail: str | None = None, evidence: str | None = None) -> CheckResult:
    return CheckResult(False, True, detail, evidence)


CheckFn = Callable[[NoteContext, dict, datetime], CheckResult]
REGISTRY: dict[str, CheckFn] = {}


def check(name: str):
    def deco(fn: CheckFn) -> CheckFn:
        REGISTRY[name] = fn
        return fn
    return deco


def _empty(v) -> bool:
    return v is None or (isinstance(v, (str, list, dict)) and len(v) == 0)


# ---------------- Part 599 ----------------

@check("required_field")
def required_field(note: NoteContext, params: dict, as_of: datetime) -> CheckResult:
    return fail() if _empty(getattr(note, params["field"], None)) else PASS


@check("start_end_times")
def start_end_times(note: NoteContext, params: dict, as_of: datetime) -> CheckResult:
    if not note.start_time or not note.end_time:
        return fail()
    if note.end_time <= note.start_time:
        return fail("End time is not after start time.")
    return PASS


@check("duration_matches_code")
def duration_matches_code(note: NoteContext, params: dict, as_of: datetime) -> CheckResult:
    ranges: dict = params.get("code_ranges", {})
    if not note.service_code or note.service_code not in ranges:
        return NA
    minutes = note.duration_minutes
    if minutes is None:
        return NA  # missing times are reported by P599-TIMES
    lo, hi = ranges[note.service_code]
    if minutes < lo or (hi is not None and minutes > hi):
        rng = f"{lo}+" if hi is None else f"{lo}-{hi}"
        return fail(f"{minutes} min documented; {note.service_code} requires {rng} min.")
    return PASS


@check("treatment_plan_active")
def treatment_plan_active(note: NoteContext, params: dict, as_of: datetime) -> CheckResult:
    tp, dos = note.treatment_plan, note.date_of_service
    if tp is None:
        return fail("No treatment plan found for this client.")
    if dos is None:
        return NA
    if tp.start_date and dos < tp.start_date:
        return fail("Date of service is before the treatment plan start date.")
    if tp.end_date and dos > tp.end_date:
        return fail("Treatment plan expired before the date of service.")
    return PASS


@check("treatment_plan_review_current")
def treatment_plan_review_current(note: NoteContext, params: dict, as_of: datetime) -> CheckResult:
    tp, dos = note.treatment_plan, note.date_of_service
    if tp is None or tp.review_due is None or dos is None:
        return NA
    if dos > tp.review_due:
        return fail(f"Review was due {tp.review_due:%m/%d/%Y}.")
    return PASS


# ---------------- OMH ----------------

@check("telehealth_field")
def telehealth_field(note: NoteContext, params: dict, as_of: datetime) -> CheckResult:
    if note.modality not in (Modality.VIDEO, Modality.AUDIO_ONLY):
        return NA
    value = getattr(note, params["field"], None)
    if value is False or _empty(value):
        return fail()
    return PASS


RISK_TERMS = re.compile(
    r"\b(suicid\w*|SI\b|HI\b|homicid\w*|self[- ]?harm\w*|cutting|kill (?:my|him|her|them)self"
    r"|overdos\w*|want(?:s|ed)? to die|end (?:my|his|her|their) life)",
    re.IGNORECASE,
)
NEGATED = re.compile(r"\b(denie[sd]|deny|denying|no|without|negative for)\b[^.]{0,40}$", re.IGNORECASE)
SAFETY_TERMS = re.compile(
    r"\b(safety plan\w*|risk assessment|C-?SSRS|columbia|means restriction|crisis line|988"
    r"|no (?:plan|intent)|denie[sd] (?:plan|intent)|level of risk|risk level|safety contract)\b",
    re.IGNORECASE,
)


@check("risk_language_has_safety_plan")
def risk_language_has_safety_plan(note: NoteContext, params: dict, as_of: datetime) -> CheckResult:
    """Safety net that never depends on the AI: risk words present -> safety documentation required.
    Negated mentions ("denies SI") don't trigger the rule."""
    text = note.text
    for m in RISK_TERMS.finditer(text):
        before = text[max(0, m.start() - 60):m.start()]
        if NEGATED.search(before):
            continue
        if SAFETY_TERMS.search(text):
            return PASS
        snippet = text[max(0, m.start() - 40):m.end() + 40].strip()
        return fail(evidence=snippet)
    return NA


# ---------------- SOAP ----------------

SOAP_HEADINGS = {
    "subjective": re.compile(r"^\s*(S|Subjective)\s*[:\-]", re.IGNORECASE | re.MULTILINE),
    "objective": re.compile(r"^\s*(O|Objective)\s*[:\-]", re.IGNORECASE | re.MULTILINE),
    "assessment": re.compile(r"^\s*(A|Assessment)\s*[:\-]", re.IGNORECASE | re.MULTILINE),
    "plan": re.compile(r"^\s*(P|Plan)\s*[:\-]", re.IGNORECASE | re.MULTILINE),
}


def split_soap(text: str) -> dict[str, str]:
    """Best-effort split of free text into SOAP sections by headings."""
    marks = []
    for name, rx in SOAP_HEADINGS.items():
        m = rx.search(text)
        if m:
            marks.append((m.start(), m.end(), name))
    marks.sort()
    out = {}
    for i, (_, end, name) in enumerate(marks):
        stop = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        out[name] = text[end:stop].strip()
    return out


@check("soap_sections_present")
def soap_sections_present(note: NoteContext, params: dict, as_of: datetime) -> CheckResult:
    sections = {k.lower(): v for k, v in note.sections.items()} or split_soap(note.text)
    missing = [s.title() for s in SOAP_HEADINGS if not sections.get(s, "").strip()]
    return fail("Missing: " + ", ".join(missing) + ".") if missing else PASS


def _shingles(text: str, k: int = 5) -> set[tuple[str, ...]]:
    words = re.findall(r"[a-z0-9']+", text.lower())
    return {tuple(words[i:i + k]) for i in range(max(0, len(words) - k + 1))}


def similarity(a: str, b: str) -> float:
    sa, sb = _shingles(a), _shingles(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


@check("cloned_content")
def cloned_content(note: NoteContext, params: dict, as_of: datetime) -> CheckResult:
    if not note.prior_note_texts or len(note.text.split()) < params.get("min_words", 40):
        return NA
    best = max(similarity(note.text, p) for p in note.prior_note_texts)
    if best >= params.get("threshold", 0.85):
        return fail(f"{best:.0%} overlap with a prior note.")
    return PASS


# ---------------- Agency ----------------

@check("timeliness")
def timeliness(note: NoteContext, params: dict, as_of: datetime) -> CheckResult:
    if note.is_signed:
        return NA
    ref = note.end_time or (datetime.combine(note.date_of_service, datetime.min.time()) if note.date_of_service else None)
    if ref is None:
        return NA
    if ref.tzinfo is None and as_of.tzinfo is not None:
        as_of = as_of.replace(tzinfo=None)
    deadline = ref + timedelta(hours=params.get("deadline_hours", 72))
    left = deadline - as_of
    if left <= timedelta(0):
        return fail(f"Past due by {int(-left.total_seconds() // 3600)} h.")
    if left <= timedelta(hours=params.get("warn_hours_before", 24)):
        return fail(f"Due in {int(left.total_seconds() // 3600)} h.")
    return PASS
