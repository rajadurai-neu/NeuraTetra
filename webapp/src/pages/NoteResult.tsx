import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { AssessmentOut, Finding } from "../api/types";
import { SeverityTag, StatusBadge } from "../components/Badges";
import { measureLabel, STATUS_LABEL } from "../lib/format";
import { useAsync } from "../lib/useAsync";

export function NoteResult() {
  const { id = "" } = useParams();
  const res = useAsync(() => api.assessment(id), [id]);
  const hist = useAsync(() => api.history(id), [id]);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [naFor, setNaFor] = useState<Finding | null>(null);

  const apply = (out: AssessmentOut, text: string) => {
    res.setData(out);
    hist.reload();
    const ch = out.changes;
    setMsg(ch && (ch.resolved.length || ch.new.length)
      ? `${text} ${ch.resolved.length} resolved, ${ch.new.length} new.`
      : text);
  };

  const run = async (fn: () => Promise<AssessmentOut>, text: string) => {
    setBusy(true);
    try { apply(await fn(), text); } catch (e) { setMsg(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };

  if (res.error) return <p className="alert" role="alert">{res.error} · <Link to="/">Back</Link></p>;
  if (!res.data) return <p className="empty">Loading…</p>;
  const { assessment: a, fix_first, assessed_at_display } = res.data;
  const open = a.findings.filter((f) => !f.not_applicable_reason);
  const na = a.findings.filter((f) => f.not_applicable_reason);

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <p className="small"><Link to="/">← Back to queue</Link></p>
          <h1>Note {a.note_id}</h1>
          <p className="muted small">
            Assessed {assessed_at_display} · rules {a.ruleset_version} · model {a.model ?? "–"}
          </p>
        </div>
        <button onClick={() => run(() => api.reassess(id), "Re-assessed.")} disabled={busy}>
          {busy ? "Working…" : "Re-assess"}
        </button>
      </div>

      <p aria-live="polite" className="small">{msg}</p>

      <section className="card" aria-labelledby="sum-h">
        <h2 id="sum-h" className="sr-only">Summary</h2>
        <div className="scores">
          <div><div className="muted small">Status</div><StatusBadge status={a.status} /></div>
          <div className="score"><div className="muted small">Overall</div><div className="value">{a.overall_score}%</div></div>
          <div className="score"><div className="muted small">Compliance</div>
            <div className="value">{a.compliance_score}%</div>
            <div className="small muted">{a.rules_passed} of {a.rules_checked} rules met</div></div>
          <div className="score"><div className="muted small">Clinical quality</div>
            <div className="value">{a.quality_score != null ? `${a.quality_score}%` : "–"}</div></div>
        </div>
      </section>

      <section className="card" aria-labelledby="fix-h">
        <h2 id="fix-h">Fix before signing</h2>
        {fix_first.length === 0 ? (
          <p>Nothing to fix. This note {STATUS_LABEL[a.status].toLowerCase()}.</p>
        ) : (
          <ol className="fix-list">
            {fix_first.map((f, i) => (
              <li key={i} className={`fix-item ${f.severity}`}>
                <div className="row"><SeverityTag severity={f.severity} /><span className="muted small">{f.element}</span></div>
                <div className="what">{f.issue}</div>
                <div className="how">How to fix: {f.fix}</div>
              </li>
            ))}
          </ol>
        )}
      </section>

      <section className="card" aria-labelledby="det-h">
        <h2 id="det-h">All findings</h2>
        {open.length === 0 && <p className="muted">No open findings.</p>}
        <ul className="fix-list">
          {open.map((f) => (
            <li key={f.rule_id} className={`fix-item ${f.severity}`}>
              <div className="row" style={{ justifyContent: "space-between" }}>
                <span className="row"><SeverityTag severity={f.severity} /><strong>{f.element}</strong></span>
                <button className="link small" onClick={() => setNaFor(f)}>Mark not applicable</button>
              </div>
              <div>{f.issue}</div>
              {f.evidence && <blockquote className="quote">“{f.evidence}”</blockquote>}
              <div className="how">How to fix: {f.suggestion}</div>
              <div className="small muted">{f.source} · {f.rule_id}{f.origin === "ai" ? " · AI review" : ""}</div>
            </li>
          ))}
        </ul>
        {na.length > 0 && (
          <>
            <h3 style={{ marginTop: 16 }}>Marked not applicable</h3>
            <ul className="fix-list">
              {na.map((f) => (
                <li key={f.rule_id} className="fix-item na">
                  <div className="row" style={{ justifyContent: "space-between" }}>
                    <strong>{f.element}</strong>
                    <button className="link small" disabled={busy}
                      onClick={() => run(() => api.clearNA(id, f.rule_id), "Not-applicable mark removed.")}>Undo</button>
                  </div>
                  <div>{f.issue}</div>
                  <div className="small">Reason: {f.not_applicable_reason}</div>
                </li>
              ))}
            </ul>
          </>
        )}
      </section>

      {a.measure_scores.length > 0 && (
        <section className="card" aria-labelledby="q-h">
          <h2 id="q-h">Clinical quality measures</h2>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Measure</th><th className="num">Score</th><th>Finding</th></tr></thead>
              <tbody>
                {a.measure_scores.map((m) => (
                  <tr key={m.measure}>
                    <td>{measureLabel(m.measure)}</td>
                    <td className="num">{m.score} / {m.max_score}</td>
                    <td>{m.finding}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <section className="card" aria-labelledby="h-h">
        <h2 id="h-h">Assessment history</h2>
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>When</th><th>Trigger</th><th>Status</th><th className="num">Score</th><th>Resolved</th><th>New</th></tr>
            </thead>
            <tbody>
              {hist.data?.slice().reverse().map((h) => (
                <tr key={h.assessment_id}>
                  <td className="small">{h.assessed_at_display}{h.is_current && <strong> (current)</strong>}</td>
                  <td className="small">{h.trigger.replace("_", " ")}</td>
                  <td><StatusBadge status={h.status} /></td>
                  <td className="num">{h.overall_score}%</td>
                  <td className="small">{h.resolved.join(", ") || "–"}</td>
                  <td className="small">{h.new.join(", ") || "–"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <NADialog
        finding={naFor}
        onClose={() => setNaFor(null)}
        onSave={(reason) => {
          const f = naFor!;
          setNaFor(null);
          run(() => api.markNA(id, f.rule_id, reason), "Marked not applicable.");
        }}
      />
    </div>
  );
}

function NADialog({ finding, onClose, onSave }: {
  finding: Finding | null; onClose: () => void; onSave: (reason: string) => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const [reason, setReason] = useState("");
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (finding && !d.open) d.showModal();
    if (!finding && d.open) d.close();
  }, [finding]);

  return (
    <dialog ref={ref} onClose={onClose} aria-labelledby="na-h">
      <form method="dialog" onSubmit={(e) => { e.preventDefault(); if (reason.trim().length >= 3) { onSave(reason.trim()); setReason(""); } }}>
        <h2 id="na-h">Mark not applicable</h2>
        <p className="small muted">{finding?.element}: {finding?.issue}</p>
        <label htmlFor="na-reason">Reason (visible to supervisors and QA)</label>
        <textarea id="na-reason" style={{ minHeight: 90, fontFamily: "inherit" }} value={reason}
          onChange={(e) => setReason(e.target.value)} required minLength={3} />
        <div className="row" style={{ justifyContent: "flex-end", marginTop: 12 }}>
          <button type="button" onClick={onClose}>Cancel</button>
          <button type="submit" className="primary" disabled={reason.trim().length < 3}>Save</button>
        </div>
      </form>
    </dialog>
  );
}
