import { useState } from "react";
import {
  Bar, BarChart, CartesianGrid, Cell, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { api } from "../api/client";
import { NOTE_TYPES, type Status } from "../api/types";
import { useAuth } from "../lib/auth";
import { fmtDate, noteTypeLabel, STATUS_ICON, STATUS_LABEL } from "../lib/format";
import { useAsync } from "../lib/useAsync";
import { useThemeColors } from "../lib/useThemeColors";

const STATUS_TOKEN: Record<Status, "--good" | "--warning" | "--critical"> = {
  meets_standards: "--good", needs_attention: "--warning", does_not_meet_standards: "--critical",
};

function Tip({ active, payload, label, fmt }: {
  active?: boolean; payload?: { value: number; payload: Record<string, unknown> }[]; label?: string;
  fmt: (v: number, p: Record<string, unknown>, label?: string) => string;
}) {
  if (!active || !payload?.length) return null;
  return <div className="chart-tooltip">{fmt(payload[0].value, payload[0].payload, label)}</div>;
}

export function Dashboard() {
  const { has } = useAuth();
  const c = useThemeColors();
  const [f, setF] = useState({ program: "", note_type: "", date_from: "", date_to: "" });
  const { data: s, error, loading } = useAsync(() => api.summary(f), [JSON.stringify(f)]);
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setF({ ...f, [k]: e.target.value });
  const axis = { stroke: c["--text-2"], fontSize: 12, tickLine: false, axisLine: false };
  const scopeText = has("qa", "admin") ? "All programs" : has("supervisor") ? "Your team" : "Your notes";

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Dashboard</h1>
          <p className="muted">{scopeText}. Current status of each note; trends by week of service.</p>
        </div>
      </div>

      <form className="filters card" onSubmit={(e) => e.preventDefault()} aria-label="Dashboard filters">
        <div>
          <label htmlFor="d-prog">Program</label>
          <input id="d-prog" value={f.program} onChange={set("program")} placeholder="All" />
        </div>
        <div>
          <label htmlFor="d-type">Note type</label>
          <select id="d-type" value={f.note_type} onChange={set("note_type")}>
            <option value="">All</option>
            {NOTE_TYPES.map((t) => <option key={t} value={t}>{noteTypeLabel(t)}</option>)}
          </select>
        </div>
        <div>
          <label htmlFor="d-from">Service from</label>
          <input id="d-from" type="date" value={f.date_from} onChange={set("date_from")} />
        </div>
        <div>
          <label htmlFor="d-to">Service to</label>
          <input id="d-to" type="date" value={f.date_to} onChange={set("date_to")} />
        </div>
      </form>

      {error && <p className="alert" role="alert">{error}</p>}
      {loading && !s && <p className="empty">Loading…</p>}
      {s && s.notes_assessed === 0 && <p className="empty card">No assessed notes in this view yet.</p>}

      {s && s.notes_assessed > 0 && (
        <>
          <section className="grid grid-4" aria-label="Key numbers">
            <div className="card stat"><div className="value">{s.notes_assessed}</div><div className="label">Notes assessed</div></div>
            <div className="card stat"><div className="value">{s.first_pass_rate ?? "–"}%</div><div className="label">Met standards at first assessment</div></div>
            <div className="card stat"><div className="value">{s.overdue_drafts}</div><div className="label">Drafts past deadline</div></div>
            <div className="card stat"><div className="value">{s.signed_with_open_issues}</div><div className="label">Signed with open issues</div></div>
          </section>

          <div className="grid grid-2">
            <section className="card" aria-labelledby="c-status">
              <h2 id="c-status">Current status</h2>
              <div style={{ height: 180 }}>
                <ResponsiveContainer>
                  <BarChart data={s.status_counts} layout="vertical" margin={{ left: 8, right: 24 }} barCategoryGap={6}>
                    <XAxis type="number" allowDecimals={false} {...axis} />
                    <YAxis type="category" dataKey="status" width={190} {...axis}
                      tickFormatter={(v: Status) => `${STATUS_ICON[v]} ${STATUS_LABEL[v]}`} />
                    <Tooltip cursor={{ fill: c["--grid"] }}
                      content={<Tip fmt={(v, p) => `${STATUS_LABEL[p.status as Status]}: ${v} notes`} />} />
                    <Bar dataKey="count" radius={[0, 4, 4, 0]} maxBarSize={28} isAnimationActive={false}>
                      {s.status_counts.map((d) => <Cell key={d.status} fill={c[STATUS_TOKEN[d.status]]} />)}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
              <details><summary>Show as table</summary>
                <table><thead><tr><th>Status</th><th className="num">Notes</th></tr></thead>
                  <tbody>{s.status_counts.map((d) => <tr key={d.status}><td>{STATUS_LABEL[d.status]}</td><td className="num">{d.count}</td></tr>)}</tbody>
                </table>
              </details>
            </section>

            <section className="card" aria-labelledby="c-trend">
              <h2 id="c-trend">Notes meeting standards, by week</h2>
              {s.trend.length < 2 ? (
                <p className="muted">Needs at least two weeks of notes. This week: {s.trend[0]?.pct_meeting ?? 0}% meeting standards.</p>
              ) : (
                <div style={{ height: 220 }}>
                  <ResponsiveContainer>
                    <LineChart data={s.trend} margin={{ left: 0, right: 16, top: 8 }}>
                      <CartesianGrid stroke={c["--grid"]} vertical={false} />
                      <XAxis dataKey="week_of" tickFormatter={fmtDate} {...axis} />
                      <YAxis domain={[0, 100]} tickFormatter={(v) => `${v}%`} width={44} {...axis} />
                      <Tooltip cursor={{ stroke: c["--text-2"], strokeDasharray: "3 3" }}
                        content={<Tip fmt={(v, p) => `Week of ${fmtDate(p.week_of as string)}: ${v}% of ${p.notes} notes`} />} />
                      <Line dataKey="pct_meeting" stroke={c["--series-1"]} strokeWidth={2}
                        dot={{ r: 4, fill: c["--series-1"], stroke: c["--surface"], strokeWidth: 2 }}
                        activeDot={{ r: 6 }} isAnimationActive={false} />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              )}
              <details><summary>Show as table</summary>
                <table><thead><tr><th>Week of</th><th className="num">Notes</th><th className="num">% meeting</th><th className="num">Avg score</th></tr></thead>
                  <tbody>{s.trend.map((t) => <tr key={t.week_of}><td>{fmtDate(t.week_of)}</td><td className="num">{t.notes}</td><td className="num">{t.pct_meeting}%</td><td className="num">{t.avg_score}%</td></tr>)}</tbody>
                </table>
              </details>
            </section>
          </div>

          <section className="card" aria-labelledby="c-issues">
            <h2 id="c-issues">Most common open issues</h2>
            <div style={{ height: Math.max(120, s.top_issues.length * 34 + 30) }}>
              <ResponsiveContainer>
                <BarChart data={s.top_issues} layout="vertical" margin={{ left: 8, right: 24 }} barCategoryGap={6}>
                  <XAxis type="number" allowDecimals={false} {...axis} />
                  <YAxis type="category" dataKey="element" width={220} {...axis} />
                  <Tooltip cursor={{ fill: c["--grid"] }}
                    content={<Tip fmt={(v, p) => `${p.element} (${p.rule_id}): ${v} notes`} />} />
                  <Bar dataKey="count" fill={c["--series-1"]} radius={[0, 4, 4, 0]} maxBarSize={22} isAnimationActive={false} />
                </BarChart>
              </ResponsiveContainer>
            </div>
            <details><summary>Show as table</summary>
              <table><thead><tr><th>Element</th><th>Rule</th><th className="num">Notes</th></tr></thead>
                <tbody>{s.top_issues.map((t) => <tr key={t.rule_id}><td>{t.element}</td><td>{t.rule_id}</td><td className="num">{t.count}</td></tr>)}</tbody>
              </table>
            </details>
          </section>

          <section className="card" aria-labelledby="c-break">
            <h2 id="c-break">By {s.breakdown_by}</h2>
            <div className="table-wrap">
              <table>
                <thead><tr><th>{s.breakdown_by === "clinician" ? "Clinician" : "Program"}</th><th className="num">Notes</th>
                  <th className="num">% meeting</th><th className="num">Critical issues</th><th className="num">Avg score</th></tr></thead>
                <tbody>
                  {s.breakdown.map((b) => (
                    <tr key={b.name}><td>{b.name}</td><td className="num">{b.notes}</td><td className="num">{b.pct_meeting}%</td>
                      <td className="num">{b.critical_issues}</td><td className="num">{b.avg_score}%</td></tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}
    </div>
  );
}
