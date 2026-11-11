"""App entry point. Local:  backend/.venv/bin/uvicorn cnat.api.main:app --app-dir backend --reload
Azure App Service startup command:  uvicorn cnat.api.main:app --host 0.0.0.0 --port 8000 --app-dir backend
"""
from cnat.api.app import create_app
from cnat.db.session import init_db, make_engine, make_session_factory
from cnat.engine.rules.ruleset import load_ruleset
from cnat.providers.ai import build_reviewer
from cnat.services.assessments import AssessmentService

_engine = make_engine()
init_db(_engine)
app = create_app(AssessmentService(make_session_factory(_engine), load_ruleset(), build_reviewer()))
