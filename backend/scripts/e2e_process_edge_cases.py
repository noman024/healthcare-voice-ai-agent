#!/usr/bin/env python3
"""
HTTP regression: mixes POST /process (Ollama) with POST /tools/invoke (deterministic SQLite).

Invoke covers booking/slots/retrieve/cancel so results do not depend on planner luck;
Invoke covers booking/slots/retrieve/cancel so results do not depend on planner luck;
/process covers greeting, identify, conversational blocked re-book after a slot is taken, goodbye.

Each run uses a rotating calendar date (shared DB may already fill 09:00 on popular test dates).

Run: cd backend && python scripts/e2e_process_edge_cases.py
Requires: API_BASE (default http://127.0.0.1:8000), Ollama, model pulled.

Ollama CLI: if `ollama` is not on PATH, use repo `./scripts/run_with_tools.sh ollama pull qwen2.5:7b-instruct`.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from datetime import date, timedelta

API = os.environ.get("API_BASE", "http://127.0.0.1:8000").rstrip("/")


def post_invoke(tool: str, arguments: dict) -> dict:
    body = json.dumps({"tool": tool, "arguments": arguments}).encode()
    req = urllib.request.Request(
        f"{API}/tools/invoke",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def post_process(message: str, session_id: str) -> dict:
    body = json.dumps(
        {"message": message, "session_id": session_id, "return_speech": False},
    ).encode()
    req = urllib.request.Request(
        f"{API}/process",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read().decode())


def main() -> int:
    seed = int(time.time()) % 100000
    suffix = seed % 10000
    # NANP exactly 11 digits (avoids truncation issues in LLM tool args).
    phone = f"+1202555{suffix:04d}"
    session = f"e2e-edge-{seed}"
    # Avoid dates already saturated with test bookings in shared SQLite.
    date_a = (date(2026, 10, 1) + timedelta(days=seed % 120)).isoformat()
    appt_early: int | None = None

    checks: list[tuple[str, bool, str]] = []

    def expect(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, ok, detail))
        sym = "OK " if ok else "BAD"
        print(f"  [{sym}] {name}" + (f" — {detail}" if detail else ""))

    print(f"API={API} session={session} phone={phone}\n")

    try:
        r1 = post_process("Hi there.", session)
        te1 = r1.get("tool_execution")
        p1 = (r1.get("plan") or {}).get("tool")
        ok1 = te1 is None or (isinstance(te1, dict) and te1.get("success") is not False)
        expect("1_greeting_no_hard_error", ok1, f"tool={p1}")
    except Exception as e:
        expect("1_greeting", False, str(e))

    try:
        inv2 = post_invoke("fetch_slots", {})
        ok2 = isinstance(inv2, dict) and inv2.get("success") is False
        expect("2_invoke_fetch_slots_requires_date", ok2, str((inv2 or {}).get("error", {}).get("code", "")))
    except Exception as e:
        expect("2_invoke_slots", False, str(e))

    try:
        post_process("What times do you have open?", f"{session}-nd")
        expect("2b_conversational_slots_query_ok", True, "")
    except Exception as e:
        expect("2b_conversational_slots_query", False, str(e))

    try:
        r3 = post_process(f"My phone is {phone}", f"{session}-id")
        te3 = r3.get("tool_execution")
        ok3 = isinstance(te3, dict) and te3.get("success") is True and te3.get("tool") == "identify_user"
        expect("3_identify_user", ok3, json.dumps(te3)[:120] if te3 else "")
    except Exception as e:
        expect("3_identify", False, str(e))

    try:
        inv4 = post_invoke("identify_user", {"phone": "+99"})
        ok4 = isinstance(inv4, dict) and inv4.get("success") is False
        expect("4_invoke_short_phone_rejected", ok4, str((inv4 or {}).get("error", {}).get("code", "")))
    except Exception as e:
        expect("4_invoke_phone", False, str(e))

    try:
        inv5 = post_invoke("fetch_slots", {"date": date_a})
        slots = (inv5.get("data") or {}).get("available_slots") or []
        ok5 = isinstance(inv5, dict) and inv5.get("success") is True and "09:00" in slots
        expect("5_invoke_fetch_slots", ok5, "")
    except Exception as e:
        expect("5_invoke_fetch", False, str(e))

    try:
        inv6 = post_invoke(
            "book_appointment",
            {"name": "Casey Test", "phone": phone, "date": date_a, "time": "09:00"},
        )
        ok6 = isinstance(inv6, dict) and inv6.get("success") is True
        if ok6:
            appt_early = int((inv6.get("data") or {}).get("appointment", {}).get("id"))
        expect("6_invoke_book_first", ok6, f"id={appt_early}")
    except Exception as e:
        expect("6_invoke_book", False, str(e))

    try:
        # Direct invoke bypasses the per-session booking gate → second insert hits SQLite DoubleBookingError.
        inv7a = post_invoke(
            "book_appointment",
            {"name": "Dup Test One", "phone": phone, "date": date_a, "time": "09:00"},
        )
        ok7a = (
            isinstance(inv7a, dict)
            and inv7a.get("success") is False
            and inv7a.get("error", {}).get("code") == "double_booking"
        )
        expect("7_invoke_double_book_blocked", ok7a, str((inv7a or {}).get("error", {}).get("code", "")))
    except Exception as e:
        expect("7_invoke_double", False, str(e))

    try:
        # Agent path: prime session gate (identify + fetch_slots) then ask for a slot that is no longer offered
        # after step 6 — expect failure (validation_error), not a duplicate DB row.
        sid_db = f"{session}-db"
        post_process(f"My phone number is {phone}.", sid_db)
        post_process(f"What times are available on {date_a}?", sid_db)
        r7 = post_process(
            f"Please book me as Morgan Lee with my number {phone} on {date_a} at 09:00.",
            sid_db,
        )
        te7 = r7.get("tool_execution")
        err = (te7 or {}).get("error") if isinstance(te7, dict) else None
        code = err.get("code", "") if isinstance(err, dict) else ""
        msg = str(err.get("message", "")).lower() if isinstance(err, dict) else ""
        ok7 = (
            isinstance(te7, dict)
            and te7.get("success") is False
            and (
                code == "double_booking"
                or (
                    code == "validation_error"
                    and (
                        # Occupied slots are omitted from fetch_slots → gate rejects the time.
                        "not in the available slots" in msg
                        or "not a clinic slot" in msg
                    )
                )
            )
        )
        expect("7b_double_book_via_process_blocked", ok7, f"code={code}")
    except Exception as e:
        expect("7_process_double", False, str(e))

    try:
        inv8 = post_invoke(
            "book_appointment",
            {"name": "Dana Other", "phone": phone, "date": date_a, "time": "10:00"},
        )
        ok8 = isinstance(inv8, dict) and inv8.get("success") is True
        expect("8_invoke_book_second_slot", ok8, "")
    except Exception as e:
        expect("8_invoke_book2", False, str(e))

    try:
        inv9 = post_invoke(
            "book_appointment",
            {"name": "Valid Name", "phone": phone, "date": "YYYY-MM-DD", "time": "11:00"},
        )
        imsg = ((inv9 or {}).get("error") or {}).get("message") or ""
        ok9 = isinstance(inv9, dict) and inv9.get("success") is False and (
            "placeholder" in imsg.lower() or "digits" in imsg.lower() or "calendar" in imsg.lower()
        )
        expect("9_invoke_template_date_rejected", ok9, imsg[:80])
    except Exception as e:
        expect("9_invoke_tpl", False, str(e))

    try:
        inv10 = post_invoke("retrieve_appointments", {"phone": phone})
        cnt = len((inv10.get("data") or {}).get("appointments") or [])
        ok10 = isinstance(inv10, dict) and inv10.get("success") is True and cnt >= 2
        expect("10_invoke_retrieve_appointments", ok10, f"count={cnt}")
    except Exception as e:
        expect("10_invoke_retrieve", False, str(e))

    aid = appt_early
    try:
        if aid is None:
            raise RuntimeError("no appointment id from step 6")
        inv11 = post_invoke(
            "cancel_appointment",
            {"appointment_id": aid, "phone": phone},
        )
        ok11 = (
            isinstance(inv11, dict)
            and inv11.get("success") is True
            and (inv11.get("data") or {}).get("appointment", {}).get("status") == "cancelled"
        )
        expect("11_invoke_cancel_by_id_and_phone", ok11, f"id={aid}")
    except Exception as e:
        expect("11_invoke_cancel", False, str(e))

    try:
        post_process("Goodbye.", f"{session}-end")
        expect("12_goodbye_returns_200", True, "")
    except Exception as e:
        expect("12_end", False, str(e))

    bad = [c for c in checks if not c[1]]
    print("\n--- summary ---")
    print(f"Passed {sum(1 for c in checks if c[1])}/{len(checks)}")
    if bad:
        for name, _, d in bad:
            print(f"  still bad: {name} {d}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
