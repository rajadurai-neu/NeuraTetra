import type {
  AssessmentOut, AuthMode, BatchResult, HistoryItem, Me, QueueItem, RulesetOut, Summary,
} from "./types";

// Local dev only: the API trusts X-Dev-User / X-Dev-Role when CNAT_DEV_AUTH=1.
// In Azure, App Service / Static Web Apps sign-in cookies identify the user instead.
const DEV_KEY = "cnat.devUser";
export interface DevUser { name: string; role: string }

export function getDevUser(): DevUser | null {
  if (!import.meta.env.DEV) return null;
  try {
    const raw = localStorage.getItem(DEV_KEY);
    return raw ? (JSON.parse(raw) as DevUser) : null;
  } catch {
    return null;
  }
}
export function setDevUser(u: DevUser | null) {
  try {
    if (u) localStorage.setItem(DEV_KEY, JSON.stringify(u));
    else localStorage.removeItem(DEV_KEY);
  } catch {
    /* storage unavailable: dev user lasts for this page only */
  }
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const dev = getDevUser();
  if (dev) {
    headers.set("X-Dev-User", dev.name);
    headers.set("X-Dev-Role", dev.role);
  }
  if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const res = await fetch(`/api${path}`, { ...init, headers, credentials: "include" });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

const qs = (params: Record<string, string | number | boolean | undefined | null>) => {
  const p = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== "") p.set(k, String(v));
  });
  const s = p.toString();
  return s ? `?${s}` : "";
};

export const api = {
  me: () => request<Me>("/me"),
  authConfig: () => request<{ mode: AuthMode }>("/auth/config"),
  login: (username: string, password: string) =>
    request<Me>("/auth/login", { method: "POST", body: JSON.stringify({ username, password }) }),
  logout: () => request<{ ok: boolean }>("/auth/logout", { method: "POST" }),
  queue: (f: Record<string, string | undefined>) => request<QueueItem[]>(`/queue${qs(f)}`),
  assessment: (id: string) => request<AssessmentOut>(`/notes/${encodeURIComponent(id)}/assessment`),
  history: (id: string) => request<HistoryItem[]>(`/notes/${encodeURIComponent(id)}/history`),
  reassess: (id: string) => request<AssessmentOut>(`/notes/${encodeURIComponent(id)}/reassess`, { method: "POST" }),
  assess: (note: Record<string, unknown>) =>
    request<AssessmentOut>("/notes/assess", { method: "POST", body: JSON.stringify(note) }),
  upload: (form: FormData) => request<BatchResult[]>("/notes/upload", { method: "POST", body: form }),
  markNA: (id: string, ruleId: string, reason: string) =>
    request<AssessmentOut>(`/notes/${encodeURIComponent(id)}/findings/${encodeURIComponent(ruleId)}/not-applicable`,
      { method: "POST", body: JSON.stringify({ reason }) }),
  clearNA: (id: string, ruleId: string) =>
    request<AssessmentOut>(`/notes/${encodeURIComponent(id)}/findings/${encodeURIComponent(ruleId)}/not-applicable`,
      { method: "DELETE" }),
  summary: (f: Record<string, string | undefined>) => request<Summary>(`/reports/summary${qs(f)}`),
  ruleset: () => request<RulesetOut>("/rulesets/current"),
};
