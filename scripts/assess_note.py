"""Assess one note (JSON sample or plain-text file) and print the result.

    backend/.venv/bin/python scripts/assess_note.py backend/tests/samples/poor_progress.json
    backend/.venv/bin/python scripts/assess_note.py my_note.txt --type progress_individual
    add --no-ai to run the rule checks only.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from cnat.domain import NoteContext, NoteType  # noqa: E402
from cnat.engine.ai.reviewer import NoAIReviewer  # noqa: E402
from cnat.engine.pipeline import assess  # noqa: E402
from cnat.engine.rules.ruleset import load_ruleset  # noqa: E402
from cnat.providers.ai import build_reviewer  # noqa: E402

ET = ZoneInfo("America/New_York")
ICON = {"critical": "✖ CRITICAL", "warning": "▲ WARNING ", "info": "• INFO    "}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--type", default="progress_individual", choices=[t.value for t in NoteType])
    ap.add_argument("--no-ai", action="store_true")
    ap.add_argument("--json", action="store_true", help="print raw JSON")
    args = ap.parse_args()

    p = Path(args.path)
    if p.suffix == ".json":
        data = json.loads(p.read_text())
        note = NoteContext(**data.get("note", data))
    else:
        note = NoteContext(note_id=p.stem, note_type=NoteType(args.type), text=p.read_text())

    reviewer = NoAIReviewer() if args.no_ai else build_reviewer()
    a = assess(note, load_ruleset(), reviewer)

    if args.json:
        print(a.model_dump_json(indent=2))
        return 0
    print(f"\nNote {a.note_id}: {a.status.value.replace('_', ' ').upper()}")
    print(f"Overall {a.overall_score}%  |  Compliance {a.compliance_score}% "
          f"({a.rules_passed}/{a.rules_checked} rules)  |  Quality {a.quality_score if a.quality_score is not None else '-'}%")
    print(f"Assessed {a.assessed_at.astimezone(ET):%m/%d/%Y %-I:%M %p} ET · rules {a.ruleset_version} · "
          f"prompt {a.prompt_version} · model {a.model}\n")
    for f in a.findings:
        tag = "N/A     " if f.not_applicable_reason else ICON[f.severity.value]
        print(f"{tag}  [{f.element}] {f.issue}")
        print(f"            Fix: {f.suggestion}   ({f.source}, {f.rule_id})")
        if f.evidence:
            print(f'            Quote: "{f.evidence}"')
    if a.measure_scores:
        print("\nQuality measures:")
        for m in a.measure_scores:
            print(f"  {m.measure:22} {m.score}/3  {m.finding}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
