"""MuseTalk routes proxy to MUSETALK_SERVICE_URL when set."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch


@patch("app.main.httpx.AsyncClient")
def test_lipsync_status_proxies_to_service(mock_client_cls, api_client, monkeypatch):
    monkeypatch.setenv("MUSETALK_SERVICE_URL", "http://musetalk.test:8001")
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"enabled": True, "ready": True}
    mock_resp.status_code = 200
    instance = MagicMock()
    instance.__aenter__ = AsyncMock(return_value=instance)
    instance.__aexit__ = AsyncMock(return_value=None)
    instance.get = AsyncMock(return_value=mock_resp)
    mock_client_cls.return_value = instance

    r = api_client.get("/avatar/lipsync/status")
    assert r.status_code == 200
    assert r.json()["ready"] is True
    instance.get.assert_awaited_once_with("http://musetalk.test:8001/avatar/lipsync/status")


@patch("app.main.httpx.AsyncClient")
def test_lipsync_post_proxies_multipart(mock_client_cls, api_client, monkeypatch):
    monkeypatch.setenv("MUSETALK_SERVICE_URL", "http://musetalk.test:8001")
    mock_resp = MagicMock()
    mock_resp.content = b"fake-mp4"
    mock_resp.status_code = 200
    mock_resp.headers = {"content-type": "video/mp4"}
    instance = MagicMock()
    instance.__aenter__ = AsyncMock(return_value=instance)
    instance.__aexit__ = AsyncMock(return_value=None)
    instance.post = AsyncMock(return_value=mock_resp)
    mock_client_cls.return_value = instance

    r = api_client.post("/avatar/lipsync", files={"audio": ("a.wav", b"RIFFxxxx", "audio/wav")})
    assert r.status_code == 200
    assert r.content == b"fake-mp4"
    instance.post.assert_awaited_once()
    call_kw = instance.post.call_args
    assert call_kw[0][0] == "http://musetalk.test:8001/avatar/lipsync"
    files_kw = call_kw[1]["files"]
    assert "audio" in files_kw


@patch("app.main.httpx.AsyncClient")
def test_avatar_reference_proxies_to_service(mock_client_cls, api_client, monkeypatch):
    monkeypatch.setenv("MUSETALK_SERVICE_URL", "http://musetalk.test:8001")
    mock_resp = MagicMock()
    mock_resp.content = b"\xff\xd8\xff"  # minimal JPEG marker prefix
    mock_resp.status_code = 200
    mock_resp.headers = {"content-type": "image/jpeg"}
    instance = MagicMock()
    instance.__aenter__ = AsyncMock(return_value=instance)
    instance.__aexit__ = AsyncMock(return_value=None)
    instance.get = AsyncMock(return_value=mock_resp)
    mock_client_cls.return_value = instance

    r = api_client.get("/avatar/reference")
    assert r.status_code == 200
    assert r.content.startswith(b"\xff\xd8")
    instance.get.assert_awaited_once_with("http://musetalk.test:8001/avatar/reference")
