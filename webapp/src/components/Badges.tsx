import type { Severity, Status } from "../api/types";
import { STATUS_CLASS, STATUS_ICON, STATUS_LABEL } from "../lib/format";

export function StatusBadge({ status }: { status: Status | null }) {
  if (!status) return <span className="badge none">Not assessed</span>;
  return (
    <span className={`badge ${STATUS_CLASS[status]}`}>
      <span className="icon" aria-hidden="true">{STATUS_ICON[status]}</span>
      {STATUS_LABEL[status]}
    </span>
  );
}

const SEV_LABEL: Record<Severity, string> = { critical: "Critical", warning: "Warning", info: "Info" };

export function SeverityTag({ severity }: { severity: Severity }) {
  return (
    <span className={`sev ${severity}`}>
      <span className="dot" aria-hidden="true" />
      {SEV_LABEL[severity]}
    </span>
  );
}
