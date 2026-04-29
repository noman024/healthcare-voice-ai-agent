"""Generate candidate appointment time slots for a business day (local template)."""

from __future__ import annotations

import os


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def day_slot_candidates() -> list[str]:
    """
    Half-hour (configurable) slots from SLOT_OPEN_HOUR through SLOT_CLOSE_HOUR exclusive
    (e.g. 9–17 → last slot 16:30 when step is 30).
    """
    open_h = _int_env("SLOT_OPEN_HOUR", 9)
    close_h = _int_env("SLOT_CLOSE_HOUR", 17)
    step = _int_env("SLOT_STEP_MINUTES", 30)
    if step <= 0:
        step = 30
    start_mins = open_h * 60
    end_mins = close_h * 60
    slots: list[str] = []
    for m in range(start_mins, end_mins, step):
        hh, mm = divmod(m, 60)
        slots.append(f"{hh:02d}:{mm:02d}")
    return slots
