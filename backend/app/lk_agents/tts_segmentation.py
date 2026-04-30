"""Split assistant text into TTS segments for lower-latency Piper + MuseTalk (chunked avatar)."""

from __future__ import annotations

import re


def split_text_for_segmented_tts(text: str, *, max_chars: int = 180) -> list[str]:
    """
    Split on sentence boundaries; join small pieces up to ``max_chars``.
    Long single spans are hard-split on word boundaries when possible.
    """
    raw = (text or "").strip()
    if not raw:
        return []

    max_chars = max(40, min(int(max_chars), 600))
    rough = re.split(r"(?<=[.!?])\s+", raw)
    sentences: list[str] = []
    for part in rough:
        p = part.strip()
        if p:
            sentences.append(p)
    if not sentences:
        return [raw] if len(raw) <= max_chars else _hard_chunk_words(raw, max_chars)

    chunks: list[str] = []
    cur = ""
    for s in sentences:
        if not s:
            continue
        candidate = f"{cur} {s}".strip() if cur else s
        if len(candidate) <= max_chars:
            cur = candidate
            continue
        if cur:
            chunks.append(cur)
        if len(s) <= max_chars:
            cur = s
        else:
            chunks.extend(_hard_chunk_words(s, max_chars))
            cur = ""
    if cur:
        chunks.append(cur)
    return chunks if chunks else [raw]


def _hard_chunk_words(s: str, max_chars: int) -> list[str]:
    if len(s) <= max_chars:
        return [s]
    words = s.split()
    out: list[str] = []
    cur = ""
    for w in words:
        cand = f"{cur} {w}".strip() if cur else w
        if len(cand) <= max_chars:
            cur = cand
            continue
        if cur:
            out.append(cur)
        if len(w) > max_chars:
            for i in range(0, len(w), max_chars):
                out.append(w[i : i + max_chars])
            cur = ""
        else:
            cur = w
    if cur:
        out.append(cur)
    return out
