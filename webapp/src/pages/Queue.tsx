import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { NOTE_TYPES } from "../api/types";
import { StatusBadge } from "../components/Badges";
import { useAuth } from "../lib/auth";
import { fmtDate, noteTypeLabel } from "../lib/format";
import { useAsync } from "../lib/useAsync";

type Filters = { status: string; program: string; note_type: string; date_from: string; date_to: string; signed: string; clinician: string };
const EMPTY: Filters = { status: "", program: "", note_type: "", date_from: "", date_to: "", signed: "false", clinician: "" };

export function Queue() {
  const { has } = useAuth();
  const [f, setF] = useState<Filters>(EMPTY);
  const { data, error, loading } = useAsync(() => api.queue(f), [JSON.stringify(f)]);
  const title = has("supervisor") ? "Team queue" : has("qa", "admin") ? "All notes" : "My drafts";
  const set = (k: keyof Filters) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setF({ ...f, [k]: e.target.value });

  return (
    <>
      <div className="page-head">
        <div>
          <h1>{title}</h1>
          <p className="muted">Critical issues must be fixed before signing.</p>
        </div>
        <Link className="btn primary" to="/new">Assess a note</Link>
      </div>

      <form className="filters card" onSubmit={(e) => e.preventDefault()} aria-label="Filters">
        <div>
          <label htmlFor="f-status">Status</label>
          <select id="f-status" value={f.status} onChange={set("status")}>
            <option value="">Any</option>
            <option value="does_not_meet_standards">Does not meet standards</option>
            <option value="needs_attention">Needs attention</option>
            <option value="meets_standards">Meets standards</option>
          </select>
        </div>
        <div>
          <label htmlFor="f-signed">Notes</label>
          <select id="f-signed" value={f.signed} onChange={set("signed")}>
            <option value="false">Drafts</option>
            <option value="true">Signed</option>
            <option value="">All</option>
          </select>
        </div>
        <div>
          <label htmlFor="f-type">Note type</label>
          <select id="f-type" value={f.note_type} onChange={set("note_type")}>
            <option value="">Any</option>
            {NOTE_TYPES.map((t) => <option key={t} value={t}>{noteTypeLabel(t)}</option>)}
          </select>
        </div>
        <div>
          <label htmlFor="f-program">Program</label>
          <input id="f-program" value={f.program} onChange={set("program")} placeholder="Any" />
        </div>
        {has("supervisor", "qa", "admin") && (
          <div>
            <label htmlFor="f-clin">Clinician</label>
            <input id="f-clin" value={f.clinician} onChange={set("clinician")} placeholder="Any" />
          </div>
        )}
        <div>
          <label htmlFor="f-from">Service from</label>
          <input id="f-from" type="date" value={f.date_from} onChange={set("date_from")} />
        </div>
        <div>
          <label htmlFor="f-to">Service to</label>
          <input id="f-to" type="date" value={f.date_to} onChange={set("date_to")} />
        </div>
        <div>
          <button type="button" onClick={() => setF(EMPTY)}>Clear filters</button>
        </div>
      </form>

      {error && <p className="alert" role="alert">{error}</p>}
      <div className="table-wrap card" style={{ padding: 0 }}>
        <table>
          <caption className="sr-only" style={{ padding: "12px 12px 0" }}>{title}</caption>
          <thead>
            <tr>
              <th>Note</th>
              <th>Date of service</th>
              {has("supervisor", "qa", "admin") && <th>Clinician</th>}
              <th>Program</th>
              <th>Status</th>
              <th className="num">Critical</th>
              <th className="num">Warnings</th>
              <th className="num">Score</th>
              <th>Last assessed</th>
            </tr>
          </thead>
          <tbody aria-busy={loading}>
            {data?.map((q) => (
              <tr key={q.note_id}>
                <td>
                  <Link to={`/notes/${encodeURIComponent(q.note_id)}`}>{noteTypeLabel(q.note_type)}</Link>
                  <div className="small muted">{q.note_id}{q.client_id ? ` · client ${q.client_id}` : ""}</div>
                </td>
                <td>{fmtDate(q.date_of_service)}</td>
                {has("supervisor", "qa", "admin") && <td>{q.clinician}</td>}
                <td>{q.program}</td>
                <td>
                  <StatusBadge status={q.status} />
                  {q.signed_with_open_issues && <div className="small">Signed with open issues</div>}
                </td>
                <td className="num">{q.critical ?? "–"}</td>
                <td className="num">{q.warning ?? "–"}</td>
                <td className="num">{q.overall_score != null ? `${q.overall_score}%` : "–"}</td>
                <td className="small">{q.assessed_at_display}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {!loading && data?.length === 0 && <p className="empty">No notes match these filters.</p>}
        {loading && !data && <p className="empty">Loading…</p>}
      </div>
    </>
  );
}
