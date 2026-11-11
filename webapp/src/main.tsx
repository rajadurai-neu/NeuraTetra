import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { AuthProvider, useAuth } from "./lib/auth";
import { Dashboard } from "./pages/Dashboard";
import { NewNote } from "./pages/NewNote";
import { NoteResult } from "./pages/NoteResult";
import { Queue } from "./pages/Queue";
import { Rules } from "./pages/Rules";
import "./styles.css";

function Gate() {
  const { me, loading, error } = useAuth();
  if (loading) return <p className="empty">Loading…</p>;
  if (!me) {
    return (
      <div className="card empty">
        <h1>Sign in</h1>
        <p>{import.meta.env.DEV ? "Choose a dev user at the top right." : "Redirecting to Microsoft sign-in…"}</p>
        {error && <p className="small muted">{error}</p>}
      </div>
    );
  }
  return (
    <Routes>
      <Route index element={<Queue />} />
      <Route path="new" element={<NewNote />} />
      <Route path="notes/:id" element={<NoteResult />} />
      <Route path="dashboard" element={<Dashboard />} />
      <Route path="rules" element={<Rules />} />
      <Route path="*" element={<p className="empty">Page not found.</p>} />
    </Routes>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route element={<Layout />}>
            <Route path="*" element={<Gate />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  </StrictMode>,
);
