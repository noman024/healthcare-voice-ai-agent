"""
Cross-transport session semantics (text vs Web_socket vs LiveKit).

The browser must send:

- ``conversation_id`` — stable for the tab; maps to ``persistence_session_id`` / SQLite
  ``conversation_messages.session_id`` so transcript rows stay under one key when the user
  switches mode or voice backend (:func:`test_conversation_id_merges_transcript_across_agent_session_ids`).
- ``session_id`` — tool/booking gate id; becomes the normalized **phone** after a successful
  ``identify_user``. The LiveKit worker only pushes ``tool_execution`` JSON on the ``va`` channel
  (no ``session_identity`` object); the UI must treat ``tool_execution.data.phone`` like
  ``session_identity.suggested_session_id`` (:func:`test_identify_user_validation`).

This module exists as a single place to grep for the contract in code review.
"""

from __future__ import annotations


def test_cross_transport_session_contract_documented() -> None:
    """Anchor test so ``pytest`` collects this module; behavior is covered in cited tests."""
    assert True
