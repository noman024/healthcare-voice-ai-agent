"""WebSocket /ws/agent mirrors iter_turn_events (plan → tool running → tool result → done)."""

from __future__ import annotations

import json


def test_ws_agent_identify_emits_running_then_tool_result(api_client, monkeypatch):
    import app.llm.ollama as ollama_mod

    def fake_chat(messages, *, client=None, timeout_s=None, response_format=None, model=None):
        sys0 = (messages[0].get("content") or "") if messages else ""
        if "finalize" in sys0.lower() or "You finalize" in sys0:
            return "Welcome Pat."
        return (
            '{"intent":"id","tool":"identify_user","arguments":{"phone":"+15551234567","name":"Pat"},'
            '"response":"Thanks."}'
        )

    monkeypatch.setattr(ollama_mod, "ollama_chat", fake_chat)

    with api_client.websocket_connect("/ws/agent") as ws:
        ws.send_text(
            json.dumps(
                {
                    "action": "turn",
                    "message": "Pat here +15551234567",
                    "session_id": "ws-ident",
                    "conversation_id": "tabsess-ws-1",
                },
            ),
        )
        plan = ws.receive_json()
        assert plan["type"] == "plan"
        assert plan["plan"]["tool"] == "identify_user"
        running = ws.receive_json()
        assert running["type"] == "tool"
        te_run = running["tool_execution"]
        assert te_run["phase"] == "running"
        assert te_run["tool"] == "identify_user"
        final_tool = ws.receive_json()
        assert final_tool["type"] == "tool"
        assert final_tool["tool_execution"]["success"] is True
        assert final_tool["tool_execution"]["tool"] == "identify_user"
        done = ws.receive_json()
        assert done["type"] == "done"
        assert "final_response" in done
