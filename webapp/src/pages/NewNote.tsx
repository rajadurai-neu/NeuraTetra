import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { NOTE_TYPES, type BatchResult } from "../api/types";
import { StatusBadge } from "../components/Badges";
import { useAuth } from "../lib/auth";
import { noteTypeLabel } from "../lib/format";

const TEMPLATE = "S: \nO: \nA: \nP: \n";

export function NewNote() {
  const { me } = useAuth();
  const nav = useNavigate();
  const [tab, setTab] = useState<"paste" | "upload">("paste");
  const [text, setText] = useState(TEMPLATE);
  const [tags, setTags] = useState({
    note_type: "progress_individual", date_of_service: "", start: "", end: "", service_code: "",
    modality: "in_person", participants: "Client", clinician_credentials: "", supervisor: "", program: "", clinic: "",
  });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [uploadResults, setUploadResults] = useState<BatchResult[] | null>(null);
  const set = (k: keyof typeof tags) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setTags({ ...tags, [k]: e.target.value });

  const toIso = (time: string) => (tags.date_of_service && time ? `${tags.date_of_service}T${time}:00` : null);

  async function submitPaste(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setErr(null);
    try {
      const out = await api.assess({
        text,
        note_type: tags.note_type,
        date_of_service: tags.date_of_service || null,
        start_time: toIso(tags.start),
        end_time: toIso(tags.end),
        service_code: tags.service_code || null,
        modality: tags.modality || null,
        participants: tags.participants.split(",").map((s) => s.trim()).filter(Boolean),
        clinician_credentials: tags.clinician_credentials || null,
        supervisor: tags.supervisor || null,
        program: tags.program || null,
        clinic: tags.clinic || null,
      });
      nav(`/notes/${encodeURIComponent(out.assessment.note_id)}`);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function submitUpload(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    setBusy(true); setErr(null); setUploadResults(null);
    try {
      setUploadResults(await api.upload(form));
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const tagFields = (
    <div className="form-grid">
      <div>
        <label htmlFor="t-type">Note type</label>
        <select id="t-type" name="note_type" value={tags.note_type} onChange={set("note_type")}>
          {NOTE_TYPES.map((t) => <option key={t} value={t}>{noteTypeLabel(t)}</option>)}
        </select>
      </div>
      <div>
        <label htmlFor="t-dos">Date of service</label>
        <input id="t-dos" name="date_of_service" type="date" value={tags.date_of_service} onChange={set("date_of_service")} />
      </div>
      <div>
        <label htmlFor="t-cred">Your credentials</label>
        <input id="t-cred" name="clinician_credentials" value={tags.clinician_credentials} onChange={set("clinician_credentials")} placeholder="e.g. LCSW" />
      </div>
      <div>
        <label htmlFor="t-sup">Supervisor</label>
        <input id="t-sup" name="supervisor" value={tags.supervisor} onChange={set("supervisor")} />
      </div>
      <div>
        <label htmlFor="t-prog">Program</label>
        <input id="t-prog" name="program" value={tags.program} onChange={set("program")} />
      </div>
      <div>
        <label htmlFor="t-clinic">Clinic</label>
        <input id="t-clinic" name="clinic" value={tags.clinic} onChange={set("clinic")} />
      </div>
    </div>
  );

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <p className="small"><Link to="/">← Back to queue</Link></p>
          <h1>Assess a note</h1>
          <p className="muted">Clinician: {me?.name}. Use synthetic or de-identified text only in Dev.</p>
        </div>
      </div>

      <div className="row" role="tablist" aria-label="How to add the note">
        <button role="tab" aria-selected={tab === "paste"} className={tab === "paste" ? "primary" : ""} onClick={() => setTab("paste")}>Paste or type</button>
        <button role="tab" aria-selected={tab === "upload"} className={tab === "upload" ? "primary" : ""} onClick={() => setTab("upload")}>Upload files</button>
      </div>

      {err && <p className="alert" role="alert">{err}</p>}

      {tab === "paste" ? (
        <form className="card stack" onSubmit={submitPaste}>
          {tagFields}
          <div className="form-grid">
            <div>
              <label htmlFor="t-start">Start time</label>
              <input id="t-start" type="time" value={tags.start} onChange={set("start")} />
            </div>
            <div>
              <label htmlFor="t-end">End time</label>
              <input id="t-end" type="time" value={tags.end} onChange={set("end")} />
            </div>
            <div>
              <label htmlFor="t-code">Service code</label>
              <input id="t-code" value={tags.service_code} onChange={set("service_code")} placeholder="e.g. 90837" />
            </div>
            <div>
              <label htmlFor="t-mod">Modality</label>
              <select id="t-mod" value={tags.modality} onChange={set("modality")}>
                <option value="in_person">In person</option>
                <option value="video">Video</option>
                <option value="audio_only">Audio only</option>
              </select>
            </div>
            <div>
              <label htmlFor="t-part">Participants (comma-separated)</label>
              <input id="t-part" value={tags.participants} onChange={set("participants")} />
            </div>
          </div>
          <div>
            <label htmlFor="t-text">Note text</label>
            <textarea id="t-text" value={text} onChange={(e) => setText(e.target.value)} required />
          </div>
          <div className="row"><button type="submit" className="primary" disabled={busy || !text.trim()}>{busy ? "Assessing…" : "Assess note"}</button></div>
        </form>
      ) : (
        <form className="card stack" onSubmit={submitUpload}>
          {tagFields}
          <div>
            <label htmlFor="t-files">Note files (.txt, up to 100)</label>
            <input id="t-files" name="files" type="file" accept=".txt,text/plain" multiple required />
            <p className="small muted">Tags above apply to every file. Word and PDF support is coming later.</p>
          </div>
          <div className="row"><button type="submit" className="primary" disabled={busy}>{busy ? "Uploading…" : "Upload and assess"}</button></div>
          {uploadResults && (
            <div className="table-wrap" aria-live="polite">
              <table>
                <caption>Upload results</caption>
                <thead><tr><th>Note</th><th>Result</th><th className="num">Score</th></tr></thead>
                <tbody>
                  {uploadResults.map((r) => (
                    <tr key={r.note_id}>
                      <td>{r.ok ? <Link to={`/notes/${encodeURIComponent(r.note_id)}`}>{r.note_id}</Link> : r.note_id}</td>
                      <td>{r.ok ? <StatusBadge status={r.status} /> : <span>Not assessed: {r.error}</span>}</td>
                      <td className="num">{r.overall_score != null ? `${r.overall_score}%` : "–"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </form>
      )}
    </div>
  );
}
