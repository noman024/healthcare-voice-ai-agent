"""Per-session booking prerequisites: verified phone + last fetch_slots list for a date."""

from __future__ import annotations

import threading
from typing import Any

from app.tools.validation import ToolValidationError

_lock = threading.Lock()
_verified_phones: dict[str, set[str]] = {}
_last_offered_slots: dict[str, dict[str, list[str]]] = {}
# Client session label (e.g. display name) -> normalized phone from identify_user
_session_primary_phone: dict[str, str] = {}
# Normalized phone -> client session labels that successfully identified as this phone
_phone_linked_sessions: dict[str, set[str]] = {}


def clear_booking_gate_for_tests() -> None:
    with _lock:
        _verified_phones.clear()
        _last_offered_slots.clear()
        _session_primary_phone.clear()
        _phone_linked_sessions.clear()


def register_verified_phone(session_id: str, phone_normalized: str) -> None:
    sid = (session_id or "").strip()
    ph = (phone_normalized or "").strip()
    if not sid or not ph:
        return
    with _lock:
        _verified_phones.setdefault(sid, set()).add(ph)
        # Canonical phone key always includes itself so fetch/book after UI handoff share state
        _verified_phones.setdefault(ph, set()).add(ph)
        _session_primary_phone[sid] = ph
        _phone_linked_sessions.setdefault(ph, set()).add(sid)


def register_offered_slots(session_id: str, date_iso: str, available_slots: list[Any]) -> None:
    sid = (session_id or "").strip()
    d = (date_iso or "").strip()
    if not sid or not d:
        return
    slots = [str(t).strip() for t in available_slots if str(t).strip()]

    def _write(target: str) -> None:
        _last_offered_slots.setdefault(target, {})[d] = list(slots)

    with _lock:
        _write(sid)
        ph = _session_primary_phone.get(sid)
        if ph and ph != sid:
            _write(ph)
        for alt in _phone_linked_sessions.get(sid, set()):
            if alt != sid:
                _write(alt)


def assert_booking_gate_ok(session_id: str | None, phone: str, date_iso: str, time_hhmm: str) -> None:
    """
    Agent/session path: require identify_user for this phone and fetch_slots for this date
    before book_appointment. Direct ``POST /tools/invoke`` passes session_id=None → skipped.
    """
    if session_id is None or not str(session_id).strip():
        return
    sid = str(session_id).strip()

    with _lock:
        verified = _verified_phones.get(sid, set())
        if phone not in verified:
            raise ToolValidationError(
                "Bookings require identify_user to succeed first with the same phone number.",
                field="phone",
            )
        by_date = _last_offered_slots.get(sid, {})
        if date_iso not in by_date:
            raise ToolValidationError(
                "Bookings require fetch_slots for this date first; then choose a time from that list.",
                field="date",
            )
        offered = by_date[date_iso]
        if time_hhmm not in offered:
            raise ToolValidationError(
                f"Time {time_hhmm} was not in the available slots for {date_iso}. "
                "Call fetch_slots again or pick a listed time.",
                field="time",
            )
