"""LiveKit data-channel protocol parsing (pure Python)."""

from __future__ import annotations

from app.livekit.protocol import parse_control_payload, summarize_control


def test_parse_ping_ws_shape():
    verb, obj = parse_control_payload(b' {"action": "ping"} ')
    assert verb == "ping"
    assert isinstance(obj, dict)


def test_parse_start_final_ws_shape():
    v, o = parse_control_payload(
        b'{"action":"start","session_id":"s1","language":null,"return_speech":true,"file_extension":".wav"}',
    )
    assert v == "start"
    meta = summarize_control(o)
    assert meta["session_id"] == "s1"
    assert meta["file_extension"] == ".wav"
    assert meta.get("conversation_id") is None

    v2, o2 = parse_control_payload(
        b'{"action":"start","session_id":"s1","conversation_id":"room-xyz","file_extension":".wav"}',
    )
    assert v2 == "start"
    meta2 = summarize_control(o2)
    assert meta2["conversation_id"] == "room-xyz"

    vf, _ = parse_control_payload(b'{"action":"finalize"}')
    assert vf == "finalize"
def test_parse_lk_agent_shorthand():
    v, o = parse_control_payload(b'{"lk_agent":"start","session_id":"ab"}')
    assert v == "start"
    meta = summarize_control(o)
    assert meta["session_id"] == "ab"

    v2, _ = parse_control_payload(b'{"lk_agent":"finalize"}')
    assert v2 == "finalize"
