import { NavLink, Outlet } from "react-router-dom";
import { getDevUser } from "../api/client";
import { DEV_USERS, useAuth } from "../lib/auth";

export function Layout() {
  const { me, mode, has, switchDevUser, signOut } = useAuth();
  const dev = getDevUser();
  const canSeeReports = has("supervisor", "qa", "admin");

  return (
    <>
      <a className="skip-link" href="#main">Skip to content</a>
      <header className="topbar">
        <span className="brand">Notes Assessment</span>
        {me && <nav className="nav" aria-label="Main">
          <NavLink to="/" end>{has("supervisor") ? "Team queue" : has("qa", "admin") ? "All notes" : "My drafts"}</NavLink>
          <NavLink to="/new">Assess a note</NavLink>
          {canSeeReports && <NavLink to="/dashboard">Dashboard</NavLink>}
          <NavLink to="/rules">Rules</NavLink>
        </nav>}
        <div className="user">
          {mode === "password" && me ? (
            <span className="small">{me.name} · <button type="button" className="linkish" onClick={signOut}>Sign out</button></span>
          ) : import.meta.env.DEV ? (
            <label className="row" style={{ margin: 0, fontWeight: 400 }}>
              <span className="small muted">Dev user</span>
              <select
                aria-label="Switch dev user"
                value={dev ? `${dev.name}|${dev.role}` : ""}
                onChange={(e) => {
                  const [name, role] = e.target.value.split("|");
                  switchDevUser(name ? { name, role } : null);
                }}
                style={{ width: "auto" }}
              >
                <option value="">Choose…</option>
                {DEV_USERS.map((u) => (
                  <option key={u.name} value={`${u.name}|${u.role}`}>{u.name} ({u.role})</option>
                ))}
              </select>
            </label>
          ) : (
            me && <span className="small">{me.name} · <button type="button" className="linkish" onClick={signOut}>Sign out</button></span>
          )}
        </div>
      </header>
      <main id="main" tabIndex={-1}>
        <Outlet />
      </main>
    </>
  );
}
