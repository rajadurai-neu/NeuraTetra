import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../api/client";
import type { SyncRun, SyncStatus } from "../api/types";

const REFRESH_MS = 30_000;

function RunBadge({ run, running }: { run: SyncRun | null; running: boolean }) {
  if (running) return <span className="badge none"><span className="icon" aria-hidden="true">↻</span>Running now</span>;
  if (!run) return <span className="badge none"><span className="icon" aria-hidden="true">–</span>Not run yet</span>;
  if (!run.ok) return <span className="badge fails"><span className="icon" aria-hidden="true">✕</span>Failed</span>;
  if (run.failed > 0) {
    return <span className="badge needs"><span className="icon" aria-hidden="true">!</span>Completed with failures</span>;
  }
  return <span className="badge meets"><span className="icon" aria-hidden="true">✓</span>Healthy</span>;
}

function hint(run: SyncRun): string | null {
  const text = [run.error ?? "", ...run.failures].join(" ").toLowerCase();
  if (text.includes("401") || text.includes("unauthorized")) {
    return "OpenEMR refused the sign-in. Check the API client is enabled and FHIR System Scopes are on in OpenEMR.";
  }
  if (!run.ok) return "CNAT couldn't reach OpenEMR. Check that the OpenEMR server is running and the OPENEMR_* settings.";
  if (text.includes("connection error")) {
    return "Notes were read from OpenEMR but couldn't be assessed: the AI review service couldn't be reached. "
      + "Check the CNAT_AI_* settings, or set CNAT_AI_PROVIDER=none for rule checks only.";
  }
  return null;
}

export function Sync() {
  const [status, setStatus] = useState<SyncStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notConnected, setNotConnected] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  async function load() {
    try {
      setStatus(await api.syncStatus());
      setError(null);
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) setNotConnected(true);
      else setError(e instanceof Error ? e.message : "Couldn't load sync status");
    }
  }

  useEffect(() => {
    load();
    const t = window.setInterval(load, REFRESH_MS);
    return () => window.clearInterval(t);
  }, []);

  async function syncNow() {
    setBusy(true);
    setMessage(null);
    try {
      const r = await api.syncNow();
      const run = r.last_run;
      setMessage(run.ok
        ? `Sync finished: ${run.assessed} assessed, ${run.unchanged} unchanged, ${run.failed} failed.`
        : "Sync failed. See the details below.");
    } catch (e) {
      setMessage(e instanceof ApiError && e.status === 409
        ? "A sync is already running. The page refreshes when it's done."
        : e instanceof Error ? e.message : "Sync failed");
    } finally {
      setBusy(false);
      load();
    }
  }

  const run = status?.last_run ?? null;
  const tip = run ? hint(run) : null;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>OpenEMR sync</h1>
          <p className="muted">Draft notes are pulled from OpenEMR and assessed automatically. Notes that haven't changed are skipped.</p>
        </div>
        {!notConnected && (
          <button className="primary" type="button" onClick={syncNow} disabled={busy || status?.running}>
            {busy || status?.running ? "Syncing…" : "Sync now"}
          </button>
        )}
      </div>

      {error && <p className="alert" role="alert">{error}</p>}
      {message && <p className="card" role="status">{message}</p>}

      {notConnected ? (
        <div className="card">
          <h2>OpenEMR isn't connected</h2>
          <p>Add the <code>OPENEMR_*</code> settings to the App Service and restart it. See <code>deploy/README.md</code>.</p>
        </div>
      ) : !status ? (
        <p className="empty">Loading…</p>
      ) : (
        <div className="stack">
          <div className="card">
            <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 12 }}>
              <div>
                <RunBadge run={run} running={status.running} />
                <p className="small muted" style={{ margin: "8px 0 0" }}>
                  {run ? <>Last run finished {run.finished_at_display}</> : "No sync has run since the app started."}
                </p>
              </div>
              <dl className="sync-facts">
                <div><dt>Source</dt><dd>{status.source ?? "–"}</dd></div>
                <div><dt>Schedule</dt><dd>{status.scheduled_every_minutes
                  ? `Every ${status.scheduled_every_minutes} min` : "Manual only"}</dd></div>
                <div><dt>Looks back</dt><dd>{status.lookback_days} days</dd></div>
              </dl>
            </div>
          </div>

          {run && (
            <>
              <div className="grid grid-4">
                <div className="card stat"><div className="value">{run.notes_seen}</div><div className="label">Notes found</div></div>
                <div className="card stat"><div className="value">{run.assessed}</div><div className="label">Assessed (new or changed)</div></div>
                <div className="card stat"><div className="value">{run.unchanged}</div><div className="label">Unchanged, skipped</div></div>
                <div className="card stat"><div className="value">{run.failed}</div><div className="label">Failed</div></div>
              </div>

              {tip && <p className="alert" role="alert">{tip}</p>}

              {(run.error || run.failures.length > 0) && (
                <div className="card">
                  <h2>Details</h2>
                  {run.error && <p><strong>Run error:</strong> {run.error}</p>}
                  {run.failures.length > 0 && (
                    <ul className="small">
                      {run.failures.map((f, i) => <li key={i}><code>{f}</code></li>)}
                    </ul>
                  )}
                </div>
              )}

              {run.assessed + run.unchanged > 0 && (
                <p><Link to="/">See the notes in the queue →</Link></p>
              )}
            </>
          )}
        </div>
      )}
    </>
  );
}
