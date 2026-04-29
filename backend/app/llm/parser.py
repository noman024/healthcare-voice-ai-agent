from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from app.llm.schema import AgentPlan

logger = logging.getLogger(__name__)


def repair_planner_dict(data: dict[str, Any]) -> dict[str, Any]:
    """
    Small instruction models often emit only {"tool", "arguments"} or omit "intent"/"response".
    Fill required AgentPlan fields so validation succeeds when the payload is otherwise usable.
    """
    out: dict[str, Any] = dict(data)
    args = out.get("arguments")
    if not isinstance(args, dict):
        out["arguments"] = {}
    tool_raw = str(out.get("tool") or "").strip()
    if not tool_raw:
        tool_raw = "none"
    out["tool"] = tool_raw
    intent_raw = str(out.get("intent") or "").strip()
    if not intent_raw:
        out["intent"] = tool_raw if tool_raw != "none" else "general"
    resp = out.get("response")
    if resp is None or (isinstance(resp, str) and not resp.strip()):
        out["response"] = "One moment."
    return out

_JSON_FENCE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)


def extract_json_object(text: str) -> dict[str, object]:
    """Parse first JSON object from model output, stripping optional ``` fences."""
    raw = text.strip()
    m = _JSON_FENCE.search(raw)
    if m:
        raw = m.group(1).strip()
    else:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            raw = raw[start : end + 1]
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON: {e}") from e
    if not isinstance(data, dict):
        raise ValueError("JSON root must be an object")
    return data


def parse_agent_plan(raw_text: str) -> AgentPlan:
    raw = extract_json_object(raw_text)
    raw = repair_planner_dict(raw)
    return AgentPlan.model_validate(raw)


def parse_plan_with_retry(
    complete_one: Callable[[list[dict[str, Any]]], str],
    messages: list[dict[str, Any]],
    *,
    max_attempts: int = 3,
) -> AgentPlan:
    """
    Ask the model (via complete_one) using `messages`, validate JSON plan, retry with repair hints.
    Mutates `messages` in place for retries (assistant + user repair turns).
    """
    last_err: str | None = None
    for attempt in range(max_attempts):
        raw = complete_one(messages)
        try:
            plan = parse_agent_plan(raw)
            if attempt > 0:
                logger.info("agent_plan_validated_after_retry attempt=%s", attempt + 1)
            return plan
        except (ValueError, ValidationError) as e:
            last_err = str(e)
            log_fn = logger.warning if attempt + 1 == max_attempts else logger.debug
            log_fn("agent_plan_parse_failed attempt=%s error=%s", attempt + 1, last_err[:500])
            messages.append({"role": "assistant", "content": raw})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "Your previous reply was not a valid JSON object matching the schema. "
                        f"Error: {last_err}. "
                        "Reply with ONLY one JSON object with keys intent, tool, arguments, response. "
                        "No markdown fences, no other text."
                    ),
                },
            )
    raise ValueError(f"Model did not return a valid plan after {max_attempts} attempts: {last_err}")
