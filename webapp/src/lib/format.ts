import type { Status } from "../api/types";

export const STATUS_LABEL: Record<Status, string> = {
  meets_standards: "Meets standards",
  needs_attention: "Needs attention",
  does_not_meet_standards: "Does not meet standards",
};

export const STATUS_CLASS: Record<Status, string> = {
  meets_standards: "meets",
  needs_attention: "needs",
  does_not_meet_standards: "fails",
};

export const STATUS_ICON: Record<Status, string> = {
  meets_standards: "✓",
  needs_attention: "!",
  does_not_meet_standards: "✕",
};

export const STATUS_COLOR_VAR: Record<Status, string> = {
  meets_standards: "var(--good)",
  needs_attention: "var(--warning)",
  does_not_meet_standards: "var(--critical)",
};

export function noteTypeLabel(t: string): string {
  return t.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}

/** Date of service "2026-11-09" -> "11/09/2026" (no time zone shift for plain dates). */
export function fmtDate(d: string | null | undefined): string {
  if (!d) return "";
  const [y, m, day] = d.slice(0, 10).split("-");
  return `${m}/${day}/${y}`;
}

export function measureLabel(id: string): string {
  return id.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}
