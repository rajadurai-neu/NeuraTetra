"""Turn OpenEMR Standard API records into the engine's NoteContext.

OpenEMR 7.x shapes used here (Standard REST API):
  patient      {pid, uuid, pubpid, ...}
  encounter    {eid|encounter|id, uuid, date "YYYY-MM-DD HH:MM:SS", facility, provider_id, supervisor_id, ...}
  soap_note    {id, date, pid, user, subjective, objective, assessment, plan}
  practitioner {id, fname, lname, valedictory (credential suffix, e.g. "LCSW"), specialty, ...}
  appointment  {pc_eid, pc_aid (provider id), pc_eventDate, pc_startTime, pc_endTime, ...}
"""
from __future__ import annotations

from datetime import date, datetime

from cnat_engine.domain import NoteContext, NoteType

# Not available from OpenEMR's standard API (or this dev setup). Rules needing them are skipped.
OPENEMR_UNAVAILABLE = [
    "service_code", "modality", "participants", "treatment_plan",
    "client_location", "telehealth_consent",
]

SECTIONS = [("subjective", "S"), ("objective", "O"), ("assessment", "A"), ("plan", "P")]


def _s(v) -> str:
    return "" if v is None else str(v).strip()


def encounter_id(enc: dict) -> str:
    return _s(enc.get("eid") or enc.get("encounter") or enc.get("id"))


def _parse_date(v) -> date | None:
    v = _s(v)
    if not v:
        return None
    try:
        return date.fromisoformat(v[:10])
    except ValueError:
        return None


def _parse_dt(d: date | None, t) -> datetime | None:
    t = _s(t)
    if not d or not t:
        return None
    try:
        hh, mm, *_ = t.split(":")
        return datetime(d.year, d.month, d.day, int(hh), int(mm))
    except (ValueError, TypeError):
        return None


def practitioner_index(practitioners: list[dict]) -> dict[str, dict]:
    return {_s(p.get("id")): p for p in practitioners if p.get("id") is not None}


def _name(p: dict | None) -> str | None:
    if not p:
        return None
    n = " ".join(x for x in (_s(p.get("fname")), _s(p.get("lname"))) if x)
    return n or None


def _credentials(p: dict | None) -> str | None:
    if not p:
        return None
    return _s(p.get("valedictory")) or _s(p.get("specialty")) or None


def soap_text(soap: dict) -> tuple[str, dict[str, str]]:
    sections = {name: _s(soap.get(name)) for name, _ in SECTIONS}
    text = "\n".join(f"{label}: {sections[name]}" for name, label in SECTIONS)
    return text, sections


def find_appointment(appts: list[dict], dos: date | None, provider_id: str) -> dict | None:
    """Same day; prefer the same provider."""
    same_day = [a for a in appts if _parse_date(a.get("pc_eventDate")) == dos]
    for a in same_day:
        if _s(a.get("pc_aid")) == provider_id:
            return a
    return same_day[0] if len(same_day) == 1 else None


def to_note_context(*, patient: dict, encounter: dict, soap: dict, practitioners: dict[str, dict],
                    appointments: list[dict], prior_texts: list[str], default_program: str | None = None,
                    signed: bool = False) -> NoteContext:
    dos = _parse_date(encounter.get("date")) or _parse_date(soap.get("date"))
    provider_id = _s(encounter.get("provider_id"))
    provider = practitioners.get(provider_id)
    supervisor = practitioners.get(_s(encounter.get("supervisor_id")))
    appt = find_appointment(appointments, dos, provider_id)
    text, sections = soap_text(soap)

    return NoteContext(
        note_id=f"openemr:soap:{_s(soap.get('id'))}",
        source="openemr",
        note_type=NoteType.PROGRESS_INDIVIDUAL,
        note_type_source="SOAP form (individual progress note)",
        text=text,
        sections=sections,
        client_id=_s(patient.get("pubpid")) or f"pid-{_s(patient.get('pid'))}",
        date_of_service=dos,
        start_time=_parse_dt(dos, appt.get("pc_startTime")) if appt else None,
        end_time=_parse_dt(dos, appt.get("pc_endTime")) if appt else None,
        clinician_name=_name(provider) or _s(soap.get("user")) or None,
        clinician_credentials=_credentials(provider),
        supervisor=_name(supervisor),
        program=default_program,
        clinic=_s(encounter.get("facility")) or None,
        prior_note_texts=prior_texts,
        is_signed=signed,
        unavailable_fields=list(OPENEMR_UNAVAILABLE),
    )
