"""Turn OpenEMR FHIR clinical notes (DocumentReference, category clinical-note) into NoteContext.

OpenEMR 7.0.3 shapes (seen on the dev server):
  DocumentReference {id, date, docStatus?, subject: Patient/<uuid>, context.encounter[0]: Encounter/<uuid>,
                     author: [Practitioner/<uuid>] or [null], content[0].attachment {contentType text/plain, data base64}}
  Patient {id, identifier[type PT].value (the chart number), name}
  Encounter {id, period.start, serviceProvider: Organization/<uuid>, participant? (often absent)}
  Practitioner {id, name[0].given/family, qualification?}
  Appointment {id, start, end, participant[].actor: Patient/<uuid> | Practitioner/<uuid>}
The "Clinical Notes" form text carries the S:/O:/A:/P: headings, so sections are split from the text.
"""
from __future__ import annotations

import base64
from datetime import date, datetime

from cnat_engine.domain import Modality, NoteContext, NoteType
from cnat_engine.rules.checks import split_soap

from cnat_api.connectors.openemr.mapper import OPENEMR_UNAVAILABLE

# Session times come from a same-day Appointment; modality from the visit's class. Without them,
# the rules needing them are skipped (see to_note_context).
FHIR_UNAVAILABLE = [f for f in OPENEMR_UNAVAILABLE if f != "modality"]

# Encounter.class (v3 ActCode) -> modality. Audio-only and video can't be told apart: both are "VR".
CLASS_MODALITY = {"AMB": Modality.IN_PERSON, "HH": Modality.IN_PERSON, "VR": Modality.VIDEO}


def ref_id(ref: dict | None, resource: str) -> str | None:
    """'Practitioner/abc' -> 'abc' (only for the expected resource type)."""
    r = (ref or {}).get("reference") or ""
    prefix = f"{resource}/"
    return r[len(prefix):] if r.startswith(prefix) else None


def note_text(doc: dict) -> str:
    for c in doc.get("content") or []:
        att = (c or {}).get("attachment") or {}
        if att.get("data") and str(att.get("contentType", "text/plain")).startswith("text/"):
            return base64.b64decode(att["data"]).decode("utf-8", errors="replace").replace("\r\n", "\n").strip()
    return ""


def _date(v) -> date | None:
    try:
        return date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None


def _dt(v) -> datetime | None:
    """FHIR instant -> naive datetime as written (OpenEMR labels clinic-local times with an offset)."""
    try:
        return datetime.fromisoformat(str(v)).replace(tzinfo=None) if v else None
    except ValueError:
        return None


def modality(encounter: dict | None) -> Modality | None:
    code = (((encounter or {}).get("class") or {}).get("code") or "").upper()
    return CLASS_MODALITY.get(code)


def find_appointment(appointments: list[dict], dos: date | None, practitioner_id: str | None) -> dict | None:
    """Same-day appointment; prefer one with the note's author. Ambiguous (several, none matching) -> None."""
    if not dos:
        return None
    same_day = [a for a in appointments if _date(a.get("start")) == dos and a.get("status") != "cancelled"]
    if practitioner_id:
        for a in same_day:
            if any(ref_id(p.get("actor"), "Practitioner") == practitioner_id for p in a.get("participant") or []):
                return a
    return same_day[0] if len(same_day) == 1 else None


def chart_number(patient: dict | None) -> str | None:
    for ident in (patient or {}).get("identifier") or []:
        codes = [c.get("code") for c in ((ident.get("type") or {}).get("coding") or [])]
        if "PT" in codes and ident.get("value"):
            return str(ident["value"])
    return None


def person_name(res: dict | None) -> str | None:
    for n in (res or {}).get("name") or []:
        full = " ".join([*(n.get("given") or []), n.get("family") or ""]).strip()
        if full:
            return full
    return None


def credentials(practitioner: dict | None) -> str | None:
    for q in (practitioner or {}).get("qualification") or []:
        code = q.get("code") or {}
        text = code.get("text") or next((c.get("code") or c.get("display") for c in code.get("coding") or []), None)
        if text:
            return str(text)
    return None


def to_note_context(*, doc: dict, patient: dict | None, encounter: dict | None, practitioner: dict | None,
                    clinic: str | None, default_program: str | None = None,
                    appointment: dict | None = None) -> NoteContext:
    text = note_text(doc)
    clinician = person_name(practitioner)
    start, end = _dt((appointment or {}).get("start")), _dt((appointment or {}).get("end"))
    mod = modality(encounter)
    unavailable = list(FHIR_UNAVAILABLE)
    if not clinician:
        unavailable += ["clinician_name", "clinician_credentials"]   # OpenEMR sent no author
    elif not credentials(practitioner):
        unavailable.append("clinician_credentials")
    if not (start and end):
        unavailable.append("start_time")       # no matching appointment: can't say times are missing
    if mod is None:
        unavailable.append("modality")

    patient_id = ref_id(doc.get("subject"), "Patient") or ""
    return NoteContext(
        note_id=f"openemr:docref:{doc['id']}",
        source="openemr",
        note_type=NoteType.PROGRESS_INDIVIDUAL,
        text=text,
        sections=split_soap(text),
        client_id=chart_number(patient) or (f"patient-{patient_id[:8]}" if patient_id else None),
        # The note's own date first: it's what the clinician entered for this session.
        date_of_service=_date(doc.get("date")) or _date(((doc.get("context") or {}).get("period") or {}).get("start"))
        or _date(((encounter or {}).get("period") or {}).get("start")),
        start_time=start if (start and end) else None,
        end_time=end if (start and end) else None,
        modality=mod,
        clinician_name=clinician,
        clinician_credentials=credentials(practitioner),
        program=default_program,
        clinic=clinic,
        is_signed=doc.get("docStatus") == "final",
        unavailable_fields=unavailable,
    )
