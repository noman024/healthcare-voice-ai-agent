"""Rotating API log file at repo ``logs/voice-agent-api.log`` (optional ``LOG_ENABLED=0`` to disable)."""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

_formatter = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")


def _parse_log_level(name: str) -> int:
    n = name.strip().upper()
    mapping = {"DEBUG": logging.DEBUG, "INFO": logging.INFO, "WARNING": logging.WARNING}
    return mapping.get(n, logging.INFO)


def setup_repo_file_logging() -> Path | None:
    """
    Attach a rotating file handler to the ``app`` package logger (once per interpreter).
    Honors ``LOG_ENABLED`` (default ``1``) and optional ``LOG_MAX_BYTES``, ``LOG_BACKUP_COUNT``.

    Path: ``backend/app/log_setup.py`` → repo root ``<repo>/logs/voice-agent-api.log``.

    Logs ``app.*``. The root logger defaults to ``WARNING``, which hides INFO-level lines;
    attaching handlers to ``logging``'s ``"app"`` namespace logger at ``LOG_LEVEL`` (default INFO) ensures
    planner, tools, parser, STT warnings, etc. reach the file.

    Optionally duplicate the **same handler instance** onto ``uvicorn.error`` / ``uvicorn.access`` when ``LOG_UVICORN=1`` (default ``0``).
    """
    if os.getenv("LOG_ENABLED", "1").strip().lower() in ("0", "false", "no"):
        return None

    repo_root = Path(__file__).resolve().parent.parent.parent
    log_dir = repo_root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / "voice-agent-api.log"

    resolved = path.resolve()
    lvl = _parse_log_level(os.getenv("LOG_LEVEL", "INFO"))

    app_log = logging.getLogger("app")
    for h in app_log.handlers:
        if isinstance(h, RotatingFileHandler):
            try:
                if Path(h.baseFilename).resolve() == resolved:
                    app_log.setLevel(lvl)
                    return resolved
            except OSError:
                continue

    try:
        max_bytes = max(4096, int(os.getenv("LOG_MAX_BYTES", str(10 * 1024 * 1024))))
    except ValueError:
        max_bytes = 10 * 1024 * 1024
    try:
        backup = max(1, min(99, int(os.getenv("LOG_BACKUP_COUNT", "4"))))
    except ValueError:
        backup = 4

    fh = RotatingFileHandler(
        path,
        maxBytes=max_bytes,
        backupCount=backup,
        encoding="utf-8",
        delay=False,
    )
    fh.setFormatter(_formatter)
    fh.setLevel(logging.DEBUG)

    app_log.setLevel(lvl)
    app_log.addHandler(fh)
    # Child loggers (app.llm.parser, …) propagate to ``app``.
    app_log.propagate = True

    if os.getenv("LOG_UVICORN", "0").strip().lower() in ("1", "true", "yes"):
        for vn in ("uvicorn.error", "uvicorn.access"):
            vlg = logging.getLogger(vn)
            vlg.setLevel(logging.INFO)
            vlg.addHandler(fh)

    logging.captureWarnings(True)
    return resolved
