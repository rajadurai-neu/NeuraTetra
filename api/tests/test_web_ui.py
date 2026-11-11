"""The API can also serve the built web app (one App Service in Azure)."""
from fastapi.testclient import TestClient

from cnat_api.db.session import init_db, make_engine, make_session_factory
from cnat_api.services.assessments import AssessmentService
from cnat_api.web.app import create_app
from cnat_api.web.security import AuthSettings
from cnat_engine.ai.reviewer import NoAIReviewer
from cnat_engine.rules.ruleset import load_ruleset
from cnat_api.config import ruleset_dir


def test_serves_spa_and_keeps_api(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<html>cnat</html>")
    (tmp_path / "assets" / "app.js").write_text("console.log(1)")
    (tmp_path / "favicon.svg").write_text("<svg/>")
    engine = make_engine("sqlite://")
    init_db(engine)
    svc = AssessmentService(make_session_factory(engine), load_ruleset(ruleset_dir()), NoAIReviewer())
    c = TestClient(create_app(svc, AuthSettings(dev_auth=True), web_dist=tmp_path))

    assert c.get("/").text == "<html>cnat</html>"
    assert c.get("/notes/abc").text == "<html>cnat</html>"        # client-side route
    assert c.get("/assets/app.js").text == "console.log(1)"
    assert c.get("/favicon.svg").text == "<svg/>"
    assert c.get("/../../etc/passwd").text == "<html>cnat</html>"  # no escaping the folder
    assert c.get("/api/health").json() == {"ok": True}
    assert c.get("/api/nope").status_code == 404
    assert c.get("/.auth/login/aad").status_code == 404           # sign-in not on: no UI, no loop
