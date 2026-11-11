"""Load versioned YAML rule sets (rules are data, not code)."""
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from cnat.domain import NoteType, Severity

DEFAULT_RULESET_DIR = Path(__file__).resolve().parents[4] / "rulesets"


class AppliesTo(BaseModel):
    note_types: list[NoteType] | None = None  # None = all
    programs: list[str] | None = None


class Rule(BaseModel):
    id: str
    element: str
    check: str
    params: dict = Field(default_factory=dict)
    severity: Severity
    issue: str
    suggestion: str
    source: str = ""
    applies_to: AppliesTo = Field(default_factory=AppliesTo)
    status: str = "active"  # active | retired

    def applies(self, note_type: NoteType, program: str | None) -> bool:
        if self.status != "active":
            return False
        a = self.applies_to
        if a.note_types and note_type not in a.note_types:
            return False
        if a.programs and program not in a.programs:
            return False
        return True


class QualityMeasure(BaseModel):
    id: str
    name: str
    description: str
    anchors: dict


class RuleSet(BaseModel):
    version: str
    rules: list[Rule]
    measures: list[QualityMeasure]
    quality_version: str
    compliance_weight: float = 0.6
    quality_weight: float = 0.4

    @property
    def full_version(self) -> str:
        return f"{self.version}+{self.quality_version}"

    def rules_for(self, note_type: NoteType, program: str | None) -> list[Rule]:
        return [r for r in self.rules if r.applies(note_type, program)]


def load_ruleset(directory: Path = DEFAULT_RULESET_DIR) -> RuleSet:
    manifest = yaml.safe_load((directory / "manifest.yaml").read_text())
    rules: list[Rule] = []
    for fname in manifest["files"]:
        doc = yaml.safe_load((directory / fname).read_text())
        for r in doc["rules"]:
            r.setdefault("source", doc["source"])
            rules.append(Rule(**r))
    ids = [r.id for r in rules]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise ValueError(f"Duplicate rule ids: {dupes}")

    q = yaml.safe_load((directory / manifest["quality_measures"]).read_text())
    scoring = manifest.get("scoring", {})
    return RuleSet(
        version=str(manifest["version"]),
        rules=rules,
        measures=[QualityMeasure(**m) for m in q["measures"]],
        quality_version=str(q["version"]),
        compliance_weight=scoring.get("compliance_weight", 0.6),
        quality_weight=scoring.get("quality_weight", 0.4),
    )
