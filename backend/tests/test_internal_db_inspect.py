"""GET /internal/db/snapshot — disabled by default (404)."""


def test_db_snapshot_404_when_disabled(api_client, monkeypatch):
    monkeypatch.setenv("ENABLE_DB_INSPECT", "0")
    r = api_client.get("/internal/db/snapshot")
    assert r.status_code == 404


def test_db_snapshot_200_when_enabled(api_client, monkeypatch):
    monkeypatch.setenv("ENABLE_DB_INSPECT", "1")
    r = api_client.get("/internal/db/snapshot")
    assert r.status_code == 200
    data = r.json()
    assert "counts" in data
    assert "appointments" in data
    assert "conversation_messages" in data
    assert data["counts"]["appointments"] >= 0


def test_db_snapshot_session_filter(api_client, monkeypatch):
    monkeypatch.setenv("ENABLE_DB_INSPECT", "1")
    r = api_client.get("/internal/db/snapshot", params={"session_id": "nope", "messages_limit": 5})
    assert r.status_code == 200
    assert r.json()["conversation_messages"] == []
