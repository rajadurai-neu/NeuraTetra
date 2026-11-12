"""HTTP API (FastAPI). Thin layer: checks access, calls the service, shapes the response."""
from __future__ import annotations

import logging

import uuid
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from cnat_api.web.schemas import (
    AssessmentOut, BatchResult, HistoryItem, LoginIn, NoteIn, NotApplicableIn, QueueItem,
)
from cnat_api.web.password_auth import COOKIE, PasswordAuth
from cnat_api.web.security import AuthSettings, Role, User, can_see_note, current_user, require, scope_filters
from cnat_engine.domain import NoteContext, NoteType
from cnat_api.services import reports
from cnat_api.services.assessments import AssessmentService
from cnat_api.timefmt import fmt_et

log = logging.getLogger("cnat.api")

MAX_BATCH = 100
MAX_UPLOAD_BYTES = 200_000


def _svc(request: Request) -> AssessmentService:
    return request.app.state.service


def _check_note_access(svc: AssessmentService, user: User, note_id: str) -> None:
    try:
        clinician, supervisor = svc.note_people(note_id)
    except KeyError:
        raise HTTPException(404, "Note not found.")
    if not can_see_note(user, clinician, supervisor):
        raise HTTPException(404, "Note not found.")  # don't reveal other people's notes exist


def _prepare_note(body: NoteIn, user: User) -> NoteContext:
    data = body.model_dump()
    data["note_id"] = data.get("note_id") or f"manual:{uuid.uuid4().hex[:12]}"
    if not data.get("clinician_name"):
        data["clinician_name"] = user.name
    elif data["clinician_name"] != user.name and not user.has(Role.SUPERVISOR, Role.QA, Role.ADMIN):
        raise HTTPException(403, "Clinicians can only submit their own notes.")
    return NoteContext(**data)


def build_router() -> APIRouter:
    r = APIRouter(prefix="/api")

    @r.get("/health")
    def health():
        return {"ok": True}

    # ---------- built-in sign-in (when App Service sign-in isn't used) ----------
    @r.get("/auth/config")
    def auth_config(request: Request):
        pw = request.app.state.password_auth
        return {"mode": "password" if pw.enabled else "easyauth"}

    @r.post("/auth/login")
    def login(body: LoginIn, request: Request, response: Response):
        pw = request.app.state.password_auth
        if not pw.enabled:
            raise HTTPException(404, "Built-in sign-in is not turned on.")
        if pw.locked(body.username.strip().lower()):
            raise HTTPException(429, "Too many attempts. Try again in a few minutes.")
        acc = pw.authenticate(body.username, body.password)
        if acc is None:
            raise HTTPException(401, "Wrong username or password.")
        response.set_cookie(COOKIE, pw.make_session(acc), max_age=int(pw.settings.session_hours * 3600),
                            httponly=True, secure=pw.settings.cookie_secure, samesite="lax", path="/")
        return {"name": acc.name, "roles": sorted(acc.roles)}

    @r.post("/auth/logout")
    def logout(response: Response):
        response.delete_cookie(COOKIE, path="/")
        return {"ok": True}

    @r.get("/me")
    def me(user: User = Depends(current_user)):
        return {"name": user.name, "roles": sorted(x.value for x in user.roles)}

    # ---------- assess ----------
    @r.post("/notes/assess", response_model=AssessmentOut)
    def assess_note(body: NoteIn, request: Request, user: User = Depends(current_user)):
        note = _prepare_note(body, user)
        svc = _svc(request)
        if body.note_id and svc_has(svc, note.note_id):
            _check_note_access(svc, user, note.note_id)  # re-assessing an existing note
        out = svc.assess_note(note, actor=user.name, trigger="on_demand")
        return AssessmentOut.build(out.assessment, out.assessment_id, out.reused, out.changes)

    @r.post("/notes/assess-batch", response_model=list[BatchResult])
    def assess_batch(body: list[NoteIn], request: Request, user: User = Depends(current_user)):
        if len(body) > MAX_BATCH:
            raise HTTPException(400, f"At most {MAX_BATCH} notes per batch.")
        results = []
        for item in body:
            note = _prepare_note(item, user)
            try:
                out = _svc(request).assess_note(note, actor=user.name, trigger="batch")
                results.append(BatchResult(note_id=note.note_id, ok=True, status=out.assessment.status.value,
                                           overall_score=out.assessment.overall_score))
            except Exception as e:  # one bad note shouldn't stop the batch
                results.append(BatchResult(note_id=note.note_id, ok=False, error=str(e)[:300]))
        return results

    @r.post("/notes/upload", response_model=list[BatchResult])
    async def upload_notes(
        request: Request,
        files: list[UploadFile] = File(...),
        note_type: NoteType = Form(NoteType.PROGRESS_INDIVIDUAL),
        clinician_name: str | None = Form(None),
        clinician_credentials: str | None = Form(None),
        supervisor: str | None = Form(None),
        program: str | None = Form(None),
        clinic: str | None = Form(None),
        date_of_service: date | None = Form(None),
        user: User = Depends(current_user),
    ):
        """Upload one or more plain-text notes (.txt) with shared tags (spec G1-G2)."""
        if len(files) > MAX_BATCH:
            raise HTTPException(400, f"At most {MAX_BATCH} files per upload.")
        results = []
        for f in files:
            name = f.filename or "note.txt"
            raw = await f.read(MAX_UPLOAD_BYTES + 1)
            if not name.lower().endswith(".txt"):
                results.append(BatchResult(note_id=name, ok=False, error="Only .txt files are supported for now."))
                continue
            if len(raw) > MAX_UPLOAD_BYTES:
                results.append(BatchResult(note_id=name, ok=False, error="File too large."))
                continue
            body = NoteIn(text=raw.decode("utf-8", errors="replace"), note_type=note_type,
                          clinician_name=clinician_name, clinician_credentials=clinician_credentials,
                          supervisor=supervisor, program=program, clinic=clinic, date_of_service=date_of_service)
            note = _prepare_note(body, user)
            out = _svc(request).assess_note(note, actor=user.name, trigger="upload")
            results.append(BatchResult(note_id=note.note_id, ok=True, status=out.assessment.status.value,
                                       overall_score=out.assessment.overall_score))
        return results

    # ---------- read ----------
    @r.get("/notes/{note_id}/assessment", response_model=AssessmentOut)
    def get_assessment(note_id: str, request: Request, user: User = Depends(current_user)):
        svc = _svc(request)
        _check_note_access(svc, user, note_id)
        a = svc.get_results(note_id, viewer=user.name)
        if a is None:
            raise HTTPException(404, "Not assessed yet.")
        return AssessmentOut.build(a)

    @r.get("/notes/{note_id}/history", response_model=list[HistoryItem])
    def get_history(note_id: str, request: Request, user: User = Depends(current_user)):
        svc = _svc(request)
        _check_note_access(svc, user, note_id)
        return [HistoryItem(**h, assessed_at_display=fmt_et(h["assessed_at"])) for h in svc.history(note_id)]

    @r.post("/notes/{note_id}/reassess", response_model=AssessmentOut)
    def reassess(note_id: str, request: Request, user: User = Depends(current_user)):
        """On demand. EHR notes: fetch the latest draft first; if the EHR can't be reached (or the note
        isn't from it), re-run on the stored copy."""
        svc = _svc(request)
        _check_note_access(svc, user, note_id)
        latest = None
        sync = getattr(request.app.state, "openemr_sync", None)
        if sync is not None and note_id.startswith("openemr:"):
            try:
                latest = sync.fetch_latest(note_id)
            except Exception as e:  # EHR down: still let the user re-run on what we have
                log.warning("Re-assess %s: couldn't fetch the latest copy from OpenEMR (%s); using stored copy",
                            note_id, e)
        if latest is not None:
            out = svc.assess_note(latest, actor=user.name, trigger="on_demand", force=True)
        else:
            out = svc.reassess(note_id, actor=user.name)
        return AssessmentOut.build(out.assessment, out.assessment_id, False, out.changes)

    # ---------- not applicable ----------
    @r.post("/notes/{note_id}/findings/{rule_id}/not-applicable", response_model=AssessmentOut)
    def mark_na(note_id: str, rule_id: str, body: NotApplicableIn, request: Request,
                user: User = Depends(current_user)):
        svc = _svc(request)
        _check_note_access(svc, user, note_id)
        out = svc.mark_not_applicable(note_id, rule_id, body.reason, user=user.name)
        return AssessmentOut.build(out.assessment, out.assessment_id, False, out.changes)

    @r.delete("/notes/{note_id}/findings/{rule_id}/not-applicable", response_model=AssessmentOut)
    def clear_na(note_id: str, rule_id: str, request: Request, user: User = Depends(current_user)):
        svc = _svc(request)
        _check_note_access(svc, user, note_id)
        out = svc.clear_not_applicable(note_id, rule_id, user=user.name)
        return AssessmentOut.build(out.assessment, out.assessment_id, False, out.changes)

    @r.post("/notes/{note_id}/signed")
    def mark_signed(note_id: str, request: Request, user: User = Depends(current_user)):
        """Normally called by the EHR sync; available here for manual/testing use."""
        svc = _svc(request)
        _check_note_access(svc, user, note_id)
        return {"note_id": note_id, "signed_with_open_issues": svc.record_signed(note_id, user=user.name)}

    # ---------- queues ----------
    @r.get("/queue", response_model=list[QueueItem])
    def queue(request: Request, status: str | None = None, program: str | None = None,
              clinician: str | None = None, note_type: str | None = None,
              date_from: date | None = None, date_to: date | None = None, signed: bool | None = None,
              limit: int = 200, offset: int = 0, user: User = Depends(current_user)):
        """Clinician: own drafts. Supervisor: team queue. QA/Admin: everything, with filters (A8.33-34)."""
        filters = dict(status=status, program=program, clinician=clinician, note_type=note_type,
                       date_from=date_from, date_to=date_to, signed=signed,
                       limit=min(max(limit, 1), 500), offset=max(offset, 0))
        filters.update(scope_filters(user))  # scope always wins over requested filters
        return [QueueItem(**q, assessed_at_display=fmt_et(q["assessed_at"])) for q in _svc(request).queue(**filters)]

    # ---------- reports ----------
    @r.get("/reports/summary")
    def report_summary(request: Request, program: str | None = None, clinician: str | None = None,
                       note_type: str | None = None, date_from: date | None = None,
                       date_to: date | None = None, user: User = Depends(current_user)):
        """Dashboard numbers within the caller's scope (clinician: self; supervisor: team; QA: all)."""
        filters = dict(program=program, clinician=clinician, note_type=note_type,
                       date_from=date_from, date_to=date_to)
        filters.update(scope_filters(user))
        return reports.summary(_svc(request).sessions, **filters)

    # ---------- rules ----------
    @r.get("/rulesets/current")
    def ruleset(request: Request, user: User = Depends(current_user)):
        rs = _svc(request).ruleset
        return {
            "version": rs.full_version,
            "rules": [r.model_dump(mode="json") for r in rs.rules],
            "quality_measures": [m.model_dump() for m in rs.measures],
        }

    @r.get("/admin/ping", dependencies=[Depends(require(Role.ADMIN))])
    def admin_ping():
        return {"ok": True}

    # ---------- OpenEMR intake ----------
    def _sync(request: Request):
        sync = getattr(request.app.state, "openemr_sync", None)
        if sync is None:
            raise HTTPException(404, "OpenEMR intake is not configured.")
        return sync

    def _report(rep) -> dict:
        if rep is None:
            return {"last_run": None}
        d = dict(vars(rep))
        d["started_at_display"] = fmt_et(rep.started_at)
        d["finished_at_display"] = fmt_et(rep.finished_at)
        return {"last_run": d}

    @r.get("/admin/openemr/status", dependencies=[Depends(require(Role.ADMIN, Role.QA))])
    def openemr_status(request: Request):
        sync = _sync(request)
        sched = getattr(request.app.state, "openemr_scheduler", None)
        return {"scheduled_every_minutes": sched.interval / 60 if sched else None,
                "source": getattr(sync.source, "label", None), "lookback_days": sync.lookback_days,
                "running": sync.running, **_report(sync.last)}

    @r.post("/admin/openemr/sync", dependencies=[Depends(require(Role.ADMIN, Role.QA))])
    def openemr_sync_now(request: Request):
        try:
            return _report(_sync(request).run_once())
        except RuntimeError as e:
            raise HTTPException(409, str(e))

    @r.post("/admin/openemr/retro", dependencies=[Depends(require(Role.ADMIN, Role.QA))])
    def openemr_retro_review(request: Request):
        """QA retrospective review: assess notes already signed in the EHR (within the lookback window)."""
        try:
            return _report(_sync(request).run_once(retro=True))
        except RuntimeError as e:
            raise HTTPException(409, str(e))

    return r


def svc_has(svc: AssessmentService, note_id: str) -> bool:
    try:
        svc.note_people(note_id)
        return True
    except KeyError:
        return False


def mount_web(app: FastAPI, dist: Path) -> None:
    """Serve the built React app; unknown paths fall back to index.html (client-side routes)."""
    index = dist / "index.html"
    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        # /.auth/* belongs to App Service sign-in. If it reaches us, sign-in isn't turned on:
        # answer 404 rather than the UI, or the UI's login redirect would loop forever.
        if path.startswith(("api/", ".auth/")):
            raise HTTPException(404, "Not found.")
        f = (dist / path).resolve()
        if path and f.is_file() and f.is_relative_to(dist.resolve()):
            return FileResponse(f)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})


def create_app(service: AssessmentService, auth_settings: AuthSettings | None = None,
               openemr_sync=None, openemr_scheduler=None, web_dist: Path | None = None,
               password_auth: PasswordAuth | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if openemr_scheduler:
            openemr_scheduler.start()
        yield
        if openemr_scheduler:
            openemr_scheduler.stop()

    app = FastAPI(title="Clinical Notes Assessment Tool", version="0.1.0", lifespan=lifespan)
    app.state.service = service
    app.state.auth_settings = auth_settings or AuthSettings()
    app.state.password_auth = password_auth or PasswordAuth()
    app.state.openemr_sync = openemr_sync
    app.state.openemr_scheduler = openemr_scheduler
    app.include_router(build_router())

    @app.exception_handler(ValueError)
    async def value_error(_, exc: ValueError):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(KeyError)
    async def key_error(_, exc: KeyError):
        return JSONResponse(status_code=404, content={"detail": "Not found."})

    if web_dist:
        mount_web(app, web_dist)
    return app
