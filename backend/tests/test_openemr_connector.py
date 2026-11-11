from urllib.parse import parse_qs

import httpx
import jwt
import pytest
import respx
from cryptography.hazmat.primitives import serialization

from cnat.config import OpenEMRSettings
from cnat.connectors.openemr.auth import ASSERTION_TYPE, OpenEMRAuth, generate_keypair
from cnat.connectors.openemr.client import OpenEMRClient

BASE = "https://emr.test"


@pytest.fixture
def settings(tmp_path, monkeypatch):
    for k in list(__import__("os").environ):
        if k.startswith("OPENEMR_"):
            monkeypatch.delenv(k)
    pem, jwk, kid = generate_keypair()
    key = tmp_path / "k.pem"
    key.write_bytes(pem)
    s = OpenEMRSettings(_env_file=None, base_url=BASE, client_id="cid", key_id=kid,
                        private_key_path=str(key), scopes="api:oemr system/patient.read")
    s._pem = pem  # for verification in tests
    return s


def test_rejects_write_scopes(settings):
    settings.scopes = "api:oemr system/patient.write"
    with pytest.raises(RuntimeError, match="Write scopes"):
        OpenEMRAuth(settings)


def test_rejects_plain_http_unless_allowed(settings):
    settings.base_url = "http://emr.test"
    with pytest.raises(RuntimeError, match="plain HTTP"):
        OpenEMRAuth(settings)
    settings.allow_insecure_http = True
    OpenEMRAuth(settings)


@respx.mock
def test_client_credentials_assertion_and_token_cache(settings):
    seen = {}

    def token_route(request):
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        seen.update(form)
        return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})

    route = respx.post(f"{BASE}/oauth2/default/token").mock(side_effect=token_route)
    auth = OpenEMRAuth(settings)
    assert auth.token() == "tok"
    assert auth.token() == "tok"
    assert route.call_count == 1  # cached

    assert seen["grant_type"] == "client_credentials"
    assert seen["client_assertion_type"] == ASSERTION_TYPE
    pub = serialization.load_pem_private_key(settings._pem, None).public_key()
    claims = jwt.decode(seen["client_assertion"], pub, algorithms=["RS384"],
                        audience=f"{BASE}/oauth2/default/token")
    assert claims["iss"] == claims["sub"] == "cid"
    assert claims["exp"] - claims["iat"] <= 300


@respx.mock
def test_get_unwraps_data_and_refreshes_on_401(settings, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)
    tokens = iter(["t1", "t2"])
    respx.post(f"{BASE}/oauth2/default/token").mock(
        side_effect=lambda r: httpx.Response(200, json={"access_token": next(tokens), "expires_in": 3600}))
    respx.get(f"{BASE}/apis/default/api/patient").mock(side_effect=[
        httpx.Response(401),
        httpx.Response(200, json={"validationErrors": [], "internalErrors": [], "data": [{"pid": "1"}]}),
    ])
    c = OpenEMRClient(settings)
    assert c.patients() == [{"pid": "1"}]


@respx.mock
def test_retries_on_server_error(settings, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)
    respx.post(f"{BASE}/oauth2/default/token").mock(
        return_value=httpx.Response(200, json={"access_token": "t", "expires_in": 3600}))
    respx.get(f"{BASE}/apis/default/api/practitioner").mock(side_effect=[
        httpx.Response(503), httpx.Response(200, json={"data": [{"id": 1}]})])
    assert OpenEMRClient(settings).practitioners() == [{"id": 1}]


def test_client_has_no_write_methods():
    assert not any(m in dir(OpenEMRClient) for m in ("post", "put", "patch", "delete"))
