import { api } from "../api/client";
import { SeverityTag } from "../components/Badges";
import { noteTypeLabel } from "../lib/format";
import { useAsync } from "../lib/useAsync";

export function Rules() {
  const { data, error } = useAsync(() => api.ruleset(), []);
  if (error) return <p className="alert" role="alert">{error}</p>;
  if (!data) return <p className="empty">Loading…</p>;
  const bySource = data.rules.reduce<Record<string, typeof data.rules>>((acc, r) => {
    (acc[r.source] ||= []).push(r);
    return acc;
  }, {});

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Rules</h1>
          <p className="muted">Rule set version {data.version}. Starter content: compliance staff review required.</p>
        </div>
      </div>
      {Object.entries(bySource).map(([source, rules]) => (
        <section className="card" key={source} aria-labelledby={`r-${source}`}>
          <h2 id={`r-${source}`}>{source}</h2>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Rule</th><th>Element</th><th>Severity</th><th>Checks</th><th>Applies to</th></tr></thead>
              <tbody>
                {rules.map((r) => (
                  <tr key={r.id}>
                    <td className="small">{r.id}</td>
                    <td>{r.element}</td>
                    <td><SeverityTag severity={r.severity} /></td>
                    <td>{r.issue}<div className="small muted">Fix: {r.suggestion}</div></td>
                    <td className="small">{r.applies_to.note_types?.map(noteTypeLabel).join(", ") ?? "All note types"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ))}
      <section className="card" aria-labelledby="r-el">
        <h2 id="r-el">Required elements by note type</h2>
        <p className="small muted">Checked by the AI review: each element must be documented in the note.
          Missing elements are reported with rule id EL-&lt;element&gt; and count toward compliance.</p>
        {Object.entries(data.required_elements).map(([type, els]) => (
          <details key={type} className="el-type">
            <summary><strong>{noteTypeLabel(type)}</strong> <span className="muted small">({els.length} elements)</span></summary>
            <div className="table-wrap">
              <table>
                <thead><tr><th>Element</th><th>Severity if missing</th><th>What it means</th></tr></thead>
                <tbody>
                  {els.map((e) => (
                    <tr key={e.id}>
                      <td>{e.name}<div className="small muted">EL-{e.id}</div></td>
                      <td><SeverityTag severity={e.severity} /></td>
                      <td>{e.description}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
        ))}
      </section>
      <section className="card" aria-labelledby="r-q">
        <h2 id="r-q">Clinical quality measures (AI review, scored 0–3)</h2>
        <ul>{data.quality_measures.map((m) => (
          <li key={m.id}><strong>{m.name}</strong>: {m.description}
            <div className="small muted">{m.applies_to.note_types?.map(noteTypeLabel).join(", ") ?? "All note types"}</div>
          </li>
        ))}</ul>
      </section>
    </div>
  );
}
