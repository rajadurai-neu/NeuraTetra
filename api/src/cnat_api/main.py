"""App entry point. Local:  python -m uvicorn cnat_api.main:app --reload
Azure App Service startup command:  python -m uvicorn cnat_api.main:app --host 0.0.0.0 --port 8000
"""
from cnat_api.config import ruleset_dir
from cnat_api.web.app import create_app
from cnat_api.db.session import init_db, make_engine, make_session_factory
from cnat_engine.rules.ruleset import load_ruleset
from cnat_api.providers.ai import build_reviewer
from cnat_api.services.assessments import AssessmentService

_engine = make_engine()
init_db(_engine)
app = create_app(AssessmentService(make_session_factory(_engine), load_ruleset(ruleset_dir()), build_reviewer()))
