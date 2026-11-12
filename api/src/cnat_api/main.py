"""App entry point.
Local:  .venv/bin/python -m uvicorn cnat_api.main:app --reload
Azure App Service startup command:  python -m uvicorn cnat_api.main:app --host 0.0.0.0 --port 8000
(see deploy/README.md; the same app also serves the built web UI when it is next to it)
"""
import logging

from cnat_engine.rules.ruleset import load_ruleset

from cnat_api.config import OpenEMRSettings, ruleset_dir, web_dist_dir
from cnat_api.db.session import init_db, make_engine, make_session_factory
from cnat_api.providers.ai import build_reviewer
from cnat_api.services.assessments import AssessmentService
from cnat_api.web.app import create_app

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("cnat")

_engine = make_engine()
init_db(_engine)
service = AssessmentService(make_session_factory(_engine), load_ruleset(ruleset_dir()), build_reviewer())

sync = scheduler = None
emr = OpenEMRSettings()
if emr.configured:
    from cnat_api.connectors.openemr.client import OpenEMRClient
    from cnat_api.jobs.openemr_sync import OpenEMRSync, SyncScheduler

    try:
        sync = OpenEMRSync(OpenEMRClient(emr), service, lookback_days=emr.lookback_days,
                           default_program=emr.default_program)
    except Exception as exc:  # bad OpenEMR settings must not take the whole app down
        log.error("OpenEMR intake: disabled, settings incomplete: %s", exc)
    if sync and emr.sync_minutes > 0:
        scheduler = SyncScheduler(sync, emr.sync_minutes)
        log.info("OpenEMR intake: polling every %s min", emr.sync_minutes)
    elif sync:
        log.info("OpenEMR intake: on demand only (OPENEMR_SYNC_MINUTES=0)")
else:
    log.info("OpenEMR intake: not configured")

web = web_dist_dir()
log.info("Web UI: %s", web or "not built (API only)")
app = create_app(service, openemr_sync=sync, openemr_scheduler=scheduler, web_dist=web)
