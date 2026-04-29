from app.llm.parser import extract_json_object, parse_agent_plan, parse_plan_with_retry


def test_extract_json_from_fence():
    raw = """Here you go:
```json
{"intent":"x","tool":"none","arguments":{},"response":"ok"}
```
"""
    d = extract_json_object(raw)
    assert d["tool"] == "none"


def test_parse_agent_plan():
    p = parse_agent_plan(
        '{"intent":"book","tool":"none","arguments":{},"response":"Sure, I can help."}',
    )
    assert p.intent == "book"
    assert p.tool == "none"


def test_parse_sparse_model_output_repaired():
    """Models often omit intent/response; repair fills AgentPlan-required fields."""
    p = parse_agent_plan(
        '{"tool":"fetch_slots","arguments":{"date":"2026-01-15"}}',
    )
    assert p.tool == "fetch_slots"
    assert p.intent == "fetch_slots"
    assert len(p.response) >= 1


def test_parse_minimal_tool_and_end():
    p = parse_agent_plan('{"tool":"none","arguments":{}}')
    assert p.tool == "none"
    assert p.intent == "general"



def test_parse_plan_with_retry_repairs():
    responses = iter(
        [
            "not valid json",
            '{"intent":"t","tool":"none","arguments":{},"response":"fixed"}',
        ],
    )

    def complete(_m):
        return next(responses)

    messages: list = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]
    plan = parse_plan_with_retry(complete, messages, max_attempts=3)
    assert plan.tool == "none"
    assert plan.response == "fixed"
    assert len(messages) > 2
