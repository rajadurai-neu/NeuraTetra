// Shapes returned by the API (see api/src/cnat_api/web/schemas.py).

export type Status = "meets_standards" | "needs_attention" | "does_not_meet_standards";
export type Severity = "critical" | "warning" | "info";
export type Role = "clinician" | "supervisor" | "qa" | "admin";

export const NOTE_TYPES = [
  "progress_individual", "progress_group", "progress_family", "psychiatric_evaluation",
  "psychosocial_assessment", "treatment_plan", "treatment_plan_review", "crisis_safety",
  "medication_management", "collateral_contact", "discharge_summary",
] as const;
export type NoteType = (typeof NOTE_TYPES)[number];

export interface Me { name: string; roles: Role[] }

export interface Finding {
  rule_id: string;
  element: string;
  source: string;
  severity: Severity;
  issue: string;
  suggestion: string;
  evidence: string | null;
  origin: "rule" | "ai";
  not_applicable_reason: string | null;
}

export interface MeasureScore { measure: string; score: number; max_score: number; finding: string }

export interface Assessment {
  note_id: string;
  status: Status;
  compliance_score: number;
  quality_score: number | null;
  overall_score: number;
  findings: Finding[];
  measure_scores: MeasureScore[];
  rules_checked: number;
  rules_passed: number;
  ruleset_version: string;
  prompt_version: string | null;
  model: string | null;
  input_hash: string;
  assessed_at: string;
}

export interface FixItem { severity: Severity; element: string; issue: string; fix: string }

export interface AssessmentOut {
  assessment_id: number | null;
  reused: boolean;
  assessed_at_display: string;
  fix_first: FixItem[];
  changes: { resolved: string[]; new: string[]; persisting: string[] } | null;
  assessment: Assessment;
}

export interface QueueItem {
  note_id: string;
  note_type: string;
  client_id: string | null;
  clinician: string | null;
  supervisor: string | null;
  program: string | null;
  clinic: string | null;
  date_of_service: string | null;
  is_signed: boolean;
  signed_with_open_issues: boolean | null;
  status: Status | null;
  overall_score: number | null;
  critical: number | null;
  warning: number | null;
  assessed_at: string | null;
  assessed_at_display: string;
}

export interface HistoryItem {
  assessment_id: number;
  assessed_at_display: string;
  trigger: string;
  status: Status;
  overall_score: number;
  critical: number;
  warning: number;
  resolved: string[];
  new: string[];
  is_current: boolean;
  ruleset_version: string;
  model: string | null;
}

export interface BatchResult { note_id: string; ok: boolean; status: Status | null; overall_score: number | null; error: string | null }

export interface Summary {
  notes_assessed: number;
  first_pass_rate: number | null;
  overdue_drafts: number;
  signed_with_open_issues: number;
  status_counts: { status: Status; count: number }[];
  top_issues: { rule_id: string; element: string; count: number }[];
  trend: { week_of: string; notes: number; pct_meeting: number; avg_score: number }[];
  breakdown_by: "clinician" | "program";
  breakdown: { name: string; notes: number; pct_meeting: number; critical_issues: number; avg_score: number }[];
}

export interface Rule {
  id: string; element: string; check: string; severity: Severity; issue: string; suggestion: string;
  source: string; status: string; applies_to: { note_types: string[] | null; programs: string[] | null };
}
export interface RulesetOut {
  version: string;
  rules: Rule[];
  quality_measures: { id: string; name: string; description: string }[];
}
