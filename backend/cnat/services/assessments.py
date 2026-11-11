"""Use cases around assessing notes. The API and scheduler call these; they handle DB, history and audit."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy.orm import Session, sessionmaker

from cnat.db import repository as repo
from cnat.db.session import unit_of_work
from cnat.domain import Assessment, NoteContext, Severity
from cnat.engine.ai.reviewer import Reviewer
from cnat.engine.pipeline import assess
from cnat.engine.rules.ruleset import RuleSet


@dataclass
class Changes:
    """What changed compared with the previous assessment of the same note (spec A7.31)."""
    resolved: list[str] = field(default_factory=list)
    new: list[str] = field(default_factory=list)
    persisting: list[str] = field(default_factory=list)


@dataclass
class Outcome:
    assessment: Assessment
    assessment_id: int
    changes: Changes
    reused: bool = False  # True when the draft hadn't changed and the stored result was returned


def _open_rule_ids(a: Assessment) -> set[str]:
    return {f.rule_id for f in a.findings if not f.not_applicable_reason}


def diff(prev: Assessment | None, cur: Assessment) -> Changes:
    p, c = (_open_rule_ids(prev) if prev else set()), _open_rule_ids(cur)
    return Changes(resolved=sorted(p - c), new=sorted(c - p), persisting=sorted(p & c))


class AssessmentService:
    def __init__(self, sessions: sessionmaker[Session], ruleset: RuleSet, reviewer: Reviewer,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self.sessions, self.ruleset, self.reviewer, self.clock = sessions, ruleset, reviewer, clock

    # ---- assess ----
    def assess_note(self, note: NoteContext, *, actor: str = "system", trigger: str = "on_demand",
                    force: bool = False) -> Outcome:
        now = self.clock()
        with unit_of_work(self.sessions) as s:
            row, changed = repo.upsert_note(s, note, now)
            repo.audit(s, actor, "note_retrieved", "note", note.note_id, now, {"source": note.source})
            current = repo.current_assessment(s, note.note_id)
            if current and not changed and not force:
                return Outcome(repo.to_domain(current), current.id, Changes(), reused=True)
            return self._run(s, note, actor, trigger, now, current)

    def _run(self, s: Session, note: NoteContext, actor: str, trigger: str, now: datetime, current) -> Outcome:
        prev = repo.to_domain(current) if current else None
        a = assess(note, self.ruleset, self.reviewer, as_of=now,
                   na_overrides=repo.na_overrides(s, note.note_id), cache=repo.DBReviewCache(s, now))
        saved = repo.save_assessment(s, a, trigger, repo.note_content_hash(note))
        ch = diff(prev, a)
        repo.audit(s, actor, "assessment_run", "note", note.note_id, now, {
            "assessment_id": saved.id, "status": a.status.value, "ruleset": a.ruleset_version,
            "prompt": a.prompt_version, "model": a.model, "trigger": trigger,
            "resolved": ch.resolved, "new": ch.new,
        })
        return Outcome(a, saved.id, ch)

    def _stored_note(self, s: Session, note_id: str) -> NoteContext:
        row = repo.get_note(s, note_id)
        if row is None:
            raise KeyError(f"Unknown note {note_id}")
        if row.text is None:
            raise ValueError("Note text no longer stored (retention); re-fetch the note from the EHR.")
        return NoteContext(**row.context, text=row.text)

    # ---- not applicable ----
    def mark_not_applicable(self, note_id: str, rule_id: str, reason: str, user: str) -> Outcome:
        if not reason.strip():
            raise ValueError("A reason is required to mark an issue not applicable.")
        now = self.clock()
        with unit_of_work(self.sessions) as s:
            repo.set_not_applicable(s, note_id, rule_id, reason.strip(), user, now)
            repo.audit(s, user, "issue_marked_not_applicable", "note", note_id, now,
                       {"rule_id": rule_id, "reason": reason.strip()})
            s.flush()
            # Re-score with the override (the AI result comes from cache, so no extra AI call).
            return self._run(s, self._stored_note(s, note_id), user, "na_update", now,
                             repo.current_assessment(s, note_id))

    # ---- signing ----
    def record_signed(self, note_id: str, user: str = "system") -> bool:
        """Record that the note was signed in the EHR. Returns True if it was signed with open issues (A7.32)."""
        now = self.clock()
        with unit_of_work(self.sessions) as s:
            row = repo.get_note(s, note_id)
            if row is None:
                raise KeyError(note_id)
            cur = repo.current_assessment(s, note_id)
            open_issues = bool(cur and (cur.critical_count or cur.warning_count))
            row.is_signed, row.signed_at, row.signed_with_open_issues = True, now, open_issues
            repo.audit(s, user, "note_signed", "note", note_id, now, {"open_issues": open_issues})
            return open_issues

    # ---- read ----
    def get_results(self, note_id: str, viewer: str) -> Assessment | None:
        now = self.clock()
        with unit_of_work(self.sessions) as s:
            cur = repo.current_assessment(s, note_id)
            repo.audit(s, viewer, "results_viewed", "note", note_id, now)
            return repo.to_domain(cur) if cur else None

    def history(self, note_id: str) -> list[dict]:
        with unit_of_work(self.sessions) as s:
            rows = repo.assessment_history(s, note_id)
            out, prev = [], None
            for r in rows:
                a = repo.to_domain(r)
                ch = diff(prev, a)
                out.append({
                    "assessment_id": r.id, "assessed_at": a.assessed_at, "trigger": r.trigger,
                    "status": a.status.value, "overall_score": a.overall_score,
                    "critical": r.critical_count, "warning": r.warning_count,
                    "resolved": ch.resolved, "new": ch.new, "is_current": r.is_current,
                    "ruleset_version": a.ruleset_version, "model": a.model,
                })
                prev = a
            return out

    def fix_first(self, note_id: str) -> list[dict]:
        """'What to fix' at a glance (B8.36): open critical issues first, then warnings."""
        a = self.get_results(note_id, viewer="system")
        if not a:
            return []
        return [{"element": f.element, "issue": f.issue, "fix": f.suggestion, "severity": f.severity.value}
                for f in a.findings if not f.not_applicable_reason and f.severity != Severity.INFO]
