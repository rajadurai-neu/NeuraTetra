import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api, ApiError, getDevUser, setDevUser, type DevUser } from "../api/client";
import type { Me, Role } from "../api/types";

interface AuthState {
  me: Me | null;
  loading: boolean;
  error: string | null;
  has: (...roles: Role[]) => boolean;
  switchDevUser: (u: DevUser | null) => void;
}

const Ctx = createContext<AuthState | null>(null);

export const DEV_USERS: DevUser[] = [
  { name: "Synthetic Clinician", role: "clinician" },
  { name: "Synthetic Supervisor", role: "supervisor" },
  { name: "QA Person", role: "qa" },
  { name: "Admin Person", role: "admin" },
];

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [devVersion, setDevVersion] = useState(0);

  useEffect(() => {
    setLoading(true);
    api.me()
      .then((m) => { setMe(m); setError(null); })
      .catch((e: unknown) => {
        setMe(null);
        if (e instanceof ApiError && e.status === 401 && !import.meta.env.DEV) {
          window.location.href = "/.auth/login/aad?post_login_redirect_uri=" + encodeURIComponent(location.pathname);
          return;
        }
        setError(e instanceof Error ? e.message : "Sign-in failed");
      })
      .finally(() => setLoading(false));
  }, [devVersion]);

  const has = useCallback((...roles: Role[]) => !!me && me.roles.some((r) => roles.includes(r)), [me]);
  const switchDevUser = useCallback((u: DevUser | null) => { setDevUser(u); setDevVersion((v) => v + 1); }, []);

  return <Ctx.Provider value={{ me, loading, error, has, switchDevUser }}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth outside AuthProvider");
  return v;
}

export { getDevUser };
