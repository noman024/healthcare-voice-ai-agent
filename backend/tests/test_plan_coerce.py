"""Planner argument/tool correction before execution."""

from __future__ import annotations

import pytest

from app.agent.plan_coerce import coerce_agent_plan
from app.llm.schema import AgentPlan
from app.tools.executor import (
    TOOL_BOOK_APPOINTMENT,
    TOOL_CANCEL_APPOINTMENT,
    TOOL_MODIFY_APPOINTMENT,
)


def mk(**kwargs) -> AgentPlan:
    d = {"intent": "x", "response": "draft", **kwargs}
    return AgentPlan(**d)


def test_coerce_strips_extra_appointment_id_from_full_book_payload():
    p = mk(
        tool=TOOL_BOOK_APPOINTMENT,
        arguments={
            "name": "Ann",
            "phone": "+14155552671",
            "date": "2026-06-24",
            "time": "10:00",
            "appointment_id": 999,
        },
    )
    out, hit = coerce_agent_plan(p)
    assert hit
    assert out.tool == TOOL_BOOK_APPOINTMENT
    assert "appointment_id" not in out.arguments
    assert out.arguments["date"] == "2026-06-24"


def test_coerce_book_with_id_but_incomplete_turns_into_cancel():
    p = mk(
        tool=TOOL_BOOK_APPOINTMENT,
        intent="oops",
        arguments={"appointment_id": 42, "phone": "+14155552671"},
    )
    out, hit = coerce_agent_plan(p)
    assert hit
    assert out.tool == TOOL_CANCEL_APPOINTMENT
    assert out.arguments == {"appointment_id": 42, "phone": "+14155552671"}


def test_coerce_none_with_id_and_phone_to_cancel():
    p = mk(
        tool="none",
        intent="ambiguous",
        arguments={"appointment_id": 1, "phone": "+14155552671"},
    )
    out, hit = coerce_agent_plan(p)
    assert hit
    assert out.tool == TOOL_CANCEL_APPOINTMENT
    assert out.arguments["appointment_id"] == 1


def test_coerce_modify_incomplete_plus_cancel_words():
    """LLM emits modify without new_date/new_time though user clearly cancels."""
    p = mk(
        tool="modify_appointment",
        intent="confused",
        arguments={"appointment_id": 99, "phone": "+14155552671"},
    )
    out, hit = coerce_agent_plan(
        p,
        user_message="Please cancel that appointment!",
    )
    assert hit
    assert out.tool == TOOL_CANCEL_APPOINTMENT
    assert out.arguments["appointment_id"] == 99


def test_coerce_modify_reschedule_phrase_not_mapped_to_cancel():
    p = mk(
        tool="modify_appointment",
        intent="move",
        arguments={"appointment_id": 99, "phone": "+14155552671"},
    )
    out, hit = coerce_agent_plan(
        p,
        user_message="Reschedule my visit to tomorrow at eleven",
    )
    assert not hit


def test_coerce_none_with_modify_fields():
    p = mk(
        tool="none",
        intent="ambiguous",
        arguments={
            "appointment_id": 1,
            "phone": "+14155552671",
            "new_date": "2026-06-25",
            "new_time": "11:30",
        },
    )
    out, hit = coerce_agent_plan(p)
    assert hit
    assert out.tool == TOOL_MODIFY_APPOINTMENT


def test_no_coerce_plain_none():
    p = mk(tool="none", arguments={})
    out, hit = coerce_agent_plan(p)
    assert not hit
    assert out is p


def test_coerce_gratitude_only_demotes_duplicate_book_attempt():
    p = mk(
        tool=TOOL_BOOK_APPOINTMENT,
        intent="oops",
        arguments={
            "name": "Pat",
            "phone": "+14155552671",
            "date": "2026-07-01",
            "time": "10:00",
        },
    )
    out, hit = coerce_agent_plan(p, user_message="Thank you very much.")
    assert hit
    assert out.tool == "none"
    assert "welcome" in out.response.lower()


@pytest.mark.parametrize(
    "bad",
    ["YYYY-MM-DD", "yyyy-mm-dd", "June-24-2026"],
)
def test_parse_date_rejects_placeholders_or_alpha(bad: str):
    from app.tools.validation import ToolValidationError, parse_date_str

    with pytest.raises(ToolValidationError, match="date|calendar|YYYYMMDD|placeholder"):
        parse_date_str(bad)
