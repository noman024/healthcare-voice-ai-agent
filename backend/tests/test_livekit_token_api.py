import pytest


def test_livekit_token_returns_503_without_env(api_client, monkeypatch):
    monkeypatch.delenv("LIVEKIT_API_KEY", raising=False)
    monkeypatch.delenv("LIVEKIT_API_SECRET", raising=False)
    r = api_client.get("/livekit/token", params={"room": "r", "identity": "u"})
    assert r.status_code == 503
    assert "detail" in r.json()


def test_livekit_token_returns_jwt_when_configured(api_client, monkeypatch):
    try:
        import livekit  # noqa: F401
    except ImportError:
        pytest.skip("livekit-api optional; pip install -r requirements-livekit.txt")

    monkeypatch.setenv("LIVEKIT_API_KEY", "devkey")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "0123456789abcdef0123456789abcdef")
    r = api_client.get("/livekit/token", params={"room": "clinic", "identity": "tester"})
    assert r.status_code == 200
    body = r.json()
    assert body["room"] == "clinic"
    assert body["identity"] == "tester"
    assert isinstance(body["token"], str) and len(body["token"]) > 20


def test_livekit_status_reflects_configuration(api_client, monkeypatch):
    monkeypatch.delenv("LIVEKIT_API_KEY", raising=False)
    monkeypatch.delenv("LIVEKIT_API_SECRET", raising=False)
    r = api_client.get("/livekit/status")
    assert r.status_code == 200
    assert r.json() == {"token_service_enabled": False}

    monkeypatch.setenv("LIVEKIT_API_KEY", "k")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "s")
    r2 = api_client.get("/livekit/status")
    assert r2.status_code == 200
    assert r2.json() == {"token_service_enabled": True}
