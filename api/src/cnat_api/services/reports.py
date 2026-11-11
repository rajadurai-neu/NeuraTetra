"""Dashboard numbers (spec A8.36-37, G9-G12). Aggregated in Python for now; move to SQL views at scale."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import timedelta

from sqlalchemy.orm import Session, sessionmaker

from cnat_api.db import repository as repo
from cnat_api.db.session import unit_of_work

STATUSES = ["meets_standards", "needs_attention", "does_not_meet_standards"]


def _week(d):
    return d - timedelta(days=d.weekday())  # Monday


def summary(sessions: sessionmaker[Session], **filters) -> dict:
    with unit_of_work(sessions) as s:
        notes = repo.report_rows(s, **filters)
        status_counts = Counter()
        issues: Counter = Counter()
        issue_labels: dict[str, str] = {}
        first_total = first_met = overdue = signed_open = 0
        by_week: dict = defaultdict(lambda: {"total": 0, "met": 0, "score_sum": 0.0})
        by_person: dict = defaultdict(lambda: {"notes": 0, "met": 0, "critical": 0, "score_sum": 0.0})
        group_key = "clinician" if filters.get("clinician") is None else "program"

        for n in notes:
            hist = n.assessments
            if not hist:
                continue
            cur = next((a for a in hist if a.is_current), hist[-1])
            status_counts[cur.status] += 1
            first = hist[0]
            first_total += 1
            first_met += first.status == "meets_standards"
            if n.signed_with_open_issues:
                signed_open += 1
            for f in cur.findings:
                if f.not_applicable_reason:
                    continue
                issues[f.rule_id] += 1
                issue_labels.setdefault(f.rule_id, f.element)
                if f.rule_id == "AGY-TIMELINESS" and "Past due" in f.issue and not n.is_signed:
                    overdue += 1
            if n.date_of_service:
                w = by_week[_week(n.date_of_service)]
                w["total"] += 1
                w["met"] += cur.status == "meets_standards"
                w["score_sum"] += cur.overall_score
            key = (n.clinician_name if group_key == "clinician" else n.program) or "(none)"
            p = by_person[key]
            p["notes"] += 1
            p["met"] += cur.status == "meets_standards"
            p["critical"] += cur.critical_count
            p["score_sum"] += cur.overall_score

        assessed = sum(status_counts.values())
        return {
            "notes_assessed": assessed,
            "first_pass_rate": round(100 * first_met / first_total, 1) if first_total else None,
            "overdue_drafts": overdue,
            "signed_with_open_issues": signed_open,
            "status_counts": [{"status": st, "count": status_counts.get(st, 0)} for st in STATUSES],
            "top_issues": [{"rule_id": r, "element": issue_labels[r], "count": c}
                           for r, c in issues.most_common(10)],
            "trend": [{"week_of": w.isoformat(), "notes": v["total"],
                       "pct_meeting": round(100 * v["met"] / v["total"], 1),
                       "avg_score": round(v["score_sum"] / v["total"], 1)}
                      for w, v in sorted(by_week.items())],
            "breakdown_by": group_key,
            "breakdown": sorted(({"name": k, "notes": v["notes"],
                                  "pct_meeting": round(100 * v["met"] / v["notes"], 1),
                                  "critical_issues": v["critical"],
                                  "avg_score": round(v["score_sum"] / v["notes"], 1)}
                                 for k, v in by_person.items()), key=lambda x: x["avg_score"]),
        }
