"""Planner precheck demotes obviously incomplete tool picks before ``execute_tool``."""

from __future__ import annotations

from app.agent.plan_precheck import apply_plan_precheck
from app.llm.schema import AgentPlan
from app.tools.executor import TOOL_BOOK_APPOINTMENT, TOOL_FETCH_SLOTS, TOOL_IDENTIFY_USER


def test_identify_user_missing_phone_demotes_to_none() -> None:
    plan = AgentPlan(
        intent="identify",
        tool=TOOL_IDENTIFY_USER,
        arguments={},
        response="I'll look you up.",
    )
    out = apply_plan_precheck(plan)
    assert out.tool == "none"
    assert "phone number" in (out.response or "").lower()


def test_fetch_slots_bad_date_demotes_to_none() -> None:
    plan = AgentPlan(
        intent="slots",
        tool=TOOL_FETCH_SLOTS,
        arguments={"date": "not-a-date"},
        response="Fetching…",
    )
    out = apply_plan_precheck(plan)
    assert out.tool == "none"


def test_book_complete_args_passes_through() -> None:
    plan = AgentPlan(
        intent="book",
        tool=TOOL_BOOK_APPOINTMENT,
        arguments={
            "name": "Ada",
            "phone": "+14155552671",
            "date": "2026-06-01",
            "time": "10:00",
        },
        response="Booking…",
    )
    assert apply_plan_precheck(plan).tool == TOOL_BOOK_APPOINTMENT
