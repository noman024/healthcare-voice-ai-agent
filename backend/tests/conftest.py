from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from app.db.database import connect, init_db

os.environ["WARMUP_MODELS"] = "0"
os.environ.setdefault("LOG_ENABLED", "0")


@pytest.fixture(autouse=True)
def _force_whisper_cpu_in_tests(monkeypatch):
    """Fast, deterministic STT in CI; avoids loading CUDA stacks during pytest."""
    monkeypatch.setenv("WHISPER_DEVICE", "cpu")
    monkeypatch.setenv("WHISPER_COMPUTE_TYPE", "int8")
    from app.audio.stt import reset_whisper_model

    reset_whisper_model()
    yield
    reset_whisper_model()


@pytest.fixture(autouse=True)
def _clear_booking_gate_between_tests():
    from app.session_booking_gate import clear_booking_gate_for_tests

    clear_booking_gate_for_tests()
    yield
    clear_booking_gate_for_tests()


@pytest.fixture
def db_conn(tmp_path):
    path = tmp_path / "test.db"
    conn = connect(path)
    init_db(conn)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "api.sqlite"))
    from app.main import app

    with TestClient(app) as c:
        yield c
