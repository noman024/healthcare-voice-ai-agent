"""
Normalize common planner mistakes before ``execute_tool``.

Instruction models confuse ``book_appointment`` vs ``cancel_appointment``, or emit
``tool: "none"`` while passing cancel-/modify-shaped ``arguments``.
"""

from __future__ import annotations

import logging
from typing import Any

from app.llm.schema import AgentPlan
from app.tools import validation as val
from app.tools.executor import (
    TOOL_BOOK_APPOINTMENT,
    TOOL_CANCEL_APPOINTMENT,
    TOOL_MODIFY_APPOINTMENT,
)

logger = logging.getLogger(__name__)


def _user_is_gratitude_or_closing_only(user_message: str | None) -> bool:
    """
    Short thanks / goodbye without scheduling content — must not trigger book_appointment
    (avoids duplicate book on “Thank you” after a successful reservation).
    """
    if not user_message or not str(user_message).strip():
        return False
    u = str(user_message).strip().lower()
    scheduling_markers = (
        "book",
        "appointment",
        "schedule",
        "slot",
        "cancel",
        "reschedule",
        "modify",
        "change",
        "time",
        "date",
        "tomorrow",
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
        "morning",
        "afternoon",
        ":00",
        " pm",
        " am",
        "april",
        "may",
        "june",
        "july",
        "august",
        "september",
        "october",
        "november",
        "december",
        "january",
        "february",
        "march",
    )
    if any(t in u for t in scheduling_markers):
        return False
    gratitude = (
        "thank you",
        "thanks",
        "thankyou",
        "appreciate",
        "much appreciated",
        "cheers",
        "goodbye",
        "bye",
        "that's all",
        "that is all",
        "thats all",
        "nothing else",
        "no thanks",
        "we're done",
        "were done",
        "all set",
    )
    if any(g in u for g in gratitude):
        return True
    if u in ("ty", "ty!", "thx", "thx!"):
        return True
    return False


def _book_fields_complete(args: dict[str, Any]) -> bool:
    n = args.get("name")
    d = args.get("date")
    t = args.get("time")
    return bool(
        n is not None
        and str(n).strip()
        and d is not None
        and str(d).strip()
        and t is not None
        and str(t).strip()
    )


def _user_wants_cancel_not_reschedule(user_message: str | None) -> bool:
    """True if wording sounds like cancel, not reschedule to a new concrete slot."""
    if not user_message or not user_message.strip():
        return False
    u = user_message.lower()
    reschedule_markers = (
        "move to",
        "move it to",
        "reschedule to",
        "reschedule ",
        "switch to",
        "change it to",
        "change my appointment to",
    )
    if any(m in u for m in reschedule_markers):
        return False
    return any(w in u for w in ("cancel", "cancellation", "call off", "don't need", "dont need"))


def coerce_agent_plan(
    plan: AgentPlan,
    *,
    user_message: str | None = None,
) -> tuple[AgentPlan, bool]:
    """
    Return a possibly adjusted plan and whether a structural repair was applied.
    """
    args = dict(plan.arguments or {})
    tool = plan.tool

    def build(t: str, new_args: dict[str, Any], intent: str | None = None) -> AgentPlan:
        return AgentPlan(
            intent=intent or plan.intent,
            tool=t,
            arguments=new_args,
            response=plan.response,
        )

    if tool == TOOL_BOOK_APPOINTMENT and _user_is_gratitude_or_closing_only(user_message):
        logger.info("plan_coerce gratitude-or-closing-only → demote book_appointment")
        return (
            AgentPlan(
                intent="closing",
                tool="none",
                arguments={},
                response=(
                    "You're welcome—glad we could get you booked. "
                    "If you need to change anything, just say so."
                ),
            ),
            True,
        )

    # --- book_appointment: strip stray appointment_id when real book fields present ---
    if tool == TOOL_BOOK_APPOINTMENT and "appointment_id" in args:
        if _book_fields_complete(args):
            fixed = {k: v for k, v in args.items() if k != "appointment_id"}
            logger.info("plan_coerce dropped stray appointment_id from book_appointment")
            return build(TOOL_BOOK_APPOINTMENT, fixed, plan.intent), True
        # Mistaken cancel: id + phone but no full book payload
        aid = args.get("appointment_id")
        ph = args.get("phone")
        if ph is not None and str(ph).strip() and not _book_fields_complete(args):
            try:
                aid_i = int(aid)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                try:
                    aid_i = int(str(aid).strip())
                except (TypeError, ValueError):
                    return plan, False
            try:
                val.normalize_phone(str(ph))
            except val.ToolValidationError:
                return plan, False
            logger.info("plan_coerce book_appointment with id+phone but incomplete book → cancel")
            return build(
                TOOL_CANCEL_APPOINTMENT,
                {"appointment_id": aid_i, "phone": ph},
                "cancel_appointment",
            ), True

    # --- modify_appointment mistaken for cancel (LLM emitted modify without new slot) ---
    if tool == TOOL_MODIFY_APPOINTMENT:
        args_in = dict(args)
        nr = args_in.get("new_date") or ""
        nt = args_in.get("new_time") or ""
        nr_s = str(nr).strip() if nr is not None else ""
        nt_s = str(nt).strip() if nt is not None else ""
        if (not nr_s or not nt_s) and _user_wants_cancel_not_reschedule(user_message):
            if "appointment_id" in args_in and "phone" in args_in:
                try:
                    aid_m = int(args_in["appointment_id"])  # type: ignore[arg-type]
                except (TypeError, ValueError):
                    try:
                        aid_m = int(str(args_in["appointment_id"]).strip())
                    except (TypeError, ValueError):
                        return plan, False
                try:
                    val.normalize_phone(str(args_in["phone"]))
                except val.ToolValidationError:
                    return plan, False
                logger.info("plan_coerce incomplete modify_args + cancel wording → cancel_appointment")
                return (
                    build(
                        TOOL_CANCEL_APPOINTMENT,
                        {"appointment_id": aid_m, "phone": args_in["phone"]},
                        "cancel_appointment",
                    ),
                    True,
                )

    # --- tool "none" but structured cancel/modify arguments ---
    if tool == "none" and "appointment_id" in args:
        aid_raw = args.get("appointment_id")
        ph_raw = args.get("phone")
        nr = args.get("new_date")
        nt = args.get("new_time")
        if ph_raw is None or str(ph_raw).strip() == "":
            return plan, False
        try:
            aid_i = int(aid_raw)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            try:
                aid_i = int(str(aid_raw).strip())
            except (TypeError, ValueError):
                return plan, False

        nr_s = nr if isinstance(nr, str) else str(nr) if nr is not None else ""
        nt_s = nt if isinstance(nt, str) else str(nt) if nt is not None else ""
        if nr_s.strip() and nt_s.strip():
            logger.info("plan_coerce none+id+phone+slots → modify_appointment")
            return (
                build(
                    TOOL_MODIFY_APPOINTMENT,
                    {
                        "appointment_id": aid_i,
                        "phone": ph_raw,
                        "new_date": nr_s.strip(),
                        "new_time": nt_s.strip(),
                    },
                    "modify_appointment",
                ),
                True,
            )

        logger.info('plan_coerce none+id+phone → cancel_appointment (no "modify" slots)')
        return (
            build(
                TOOL_CANCEL_APPOINTMENT,
                {"appointment_id": aid_i, "phone": ph_raw},
                "cancel_appointment",
            ),
            True,
        )

    return plan, False
