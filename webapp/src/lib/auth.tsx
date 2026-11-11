import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api, ApiError, getDevUser, setDevUser, type DevUser } from "../api/client";
import type { AuthMode, Me, Role } from "../api/types";

interface AuthState {
  me: Me | null;
  loading: boolean;
  error: string | null;
  /** "password": the app's own sign-in page; "easyauth": App Service (Microsoft) sign-in. */
  mode: AuthMode | null;
  has: (...roles: Role[]) => boolean;
  switchDevUser: (u: DevUser | null) => void;
  signIn: (username: string, password: string) => Promise<void>;
  signOut: () => Promise<void>;
}

const Ctx = createContext<AuthState | null>(null);

export const DEV_USERS: DevUser[] = [
  { name: "Synthetic Clinician", role: "clinician" },
  { name: "Synthetic Supervisor", role: "supervisor" },
  { name: "QA Person", role: "qa" },
  { name: "Admin Person", role: "admin" },
];

// Send the browser to App Service sign-in, at most once per minute, so a misconfigured
// site shows an error instead of redirecting forever.
const SIGNIN_KEY = "cnat.signInAt";
const SIGNIN_RETRY_MS = 60_000;

function startSignIn(): boolean {
  if (location.pathname.startsWith("/.auth/")) return false;  // sign-in route served by the app: sign-in is off
  try {
    const last = Number(sessionStorage.getItem(SIGNIN_KEY) || 0);
    if (Date.now() - last < SIGNIN_RETRY_MS) return false;
    sessionStorage.setItem(SIGNIN_KEY, String(Date.now()));
  } catch {
    /* storage unavailable: the path check above still prevents the loop */
  }
  const back = location.pathname + location.search;
  window.location.href = "/.auth/login/aad?post_login_redirect_uri=" + encodeURIComponent(back);
  return true;
}

function clearSignInAttempt() {
  try {
    sessionStorage.removeItem(SIGNIN_KEY);
  } catch {
    /* ignore */
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState<AuthMode | null>(null);
  const [version, setVersion] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    (async () => {
      try {
        const m = await api.me();
        if (cancelled) return;
        setMe(m);
        setError(null);
        clearSignInAttempt();
        api.authConfig().then((c) => !cancelled && setMode(c.mode)).catch(() => undefined);
      } catch (e: unknown) {
        if (cancelled) return;
        setMe(null);
        if (e instanceof ApiError && e.status === 401) {
          const cfg = await api.authConfig().catch(() => ({ mode: "easyauth" as AuthMode }));
          if (cancelled) return;
          setMode(cfg.mode);
          if (cfg.mode === "password" || import.meta.env.DEV) return; // sign-in form / dev user picker
          if (startSignIn()) return;
          setError("Sign-in didn't complete. If this keeps happening, App Service sign-in (Authentication) "
            + "may not be turned on for this site.");
          return;
        }
        setError(e instanceof Error ? e.message : "Sign-in failed");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [version]);

  const has = useCallback((...roles: Role[]) => !!me && me.roles.some((r) => roles.includes(r)), [me]);
  const switchDevUser = useCallback((u: DevUser | null) => { setDevUser(u); setVersion((v) => v + 1); }, []);
  const signIn = useCallback(async (username: string, password: string) => {
    await api.login(username, password);   // throws ApiError with a readable message on failure
    setVersion((v) => v + 1);
  }, []);
  const signOut = useCallback(async () => {
    if (mode === "password") {
      await api.logout().catch(() => undefined);
      setMe(null);
      setVersion((v) => v + 1);
    } else {
      window.location.href = "/.auth/logout";
    }
  }, [mode]);

  return (
    <Ctx.Provider value={{ me, loading, error, mode, has, switchDevUser, signIn, signOut }}>
      {children}
    </Ctx.Provider>
  );
}

export function useAuth(): AuthState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth outside AuthProvider");
  return v;
}

export { getDevUser };
