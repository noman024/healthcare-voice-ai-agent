from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.tools.executor import TOOL_NAMES

ALLOWED_PLAN_TOOLS = TOOL_NAMES | {"none"}


class AgentPlan(BaseModel):
    """Structured first-pass output from the planner model (JSON only)."""

    intent: str = Field(..., min_length=1, description="Short intent label for logging/routing")
    tool: str = Field(..., min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    response: str = Field(
        ...,
        min_length=1,
        description="Draft natural-language reply (may be refined after tools run)",
    )

    @field_validator("response", mode="before")
    @classmethod
    def coerce_non_empty_response(cls, v: Any) -> str:
        """Small models sometimes emit `\"response\": \"\"`; normalize so the turn can proceed."""
        if v is None:
            return "One moment."
        s = str(v).strip()
        return s if s else "One moment."

    @field_validator("tool")
    @classmethod
    def normalize_tool(cls, v: str) -> str:
        t = v.strip()
        if t not in ALLOWED_PLAN_TOOLS:
            raise ValueError(
                f"Invalid tool '{t}'. Must be one of: {', '.join(sorted(ALLOWED_PLAN_TOOLS))}",
            )
        return t
