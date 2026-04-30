"""Unit tests for segmented TTS text splitting."""

from app.lk_agents.tts_segmentation import split_text_for_segmented_tts


def test_empty_returns_empty():
    assert split_text_for_segmented_tts("") == []
    assert split_text_for_segmented_tts("   ") == []


def test_single_short_sentence():
    assert split_text_for_segmented_tts("Hello.", max_chars=180) == ["Hello."]


def test_joins_sentences_under_max():
    text = "First. Second."
    out = split_text_for_segmented_tts(text, max_chars=80)
    assert out == ["First. Second."]


def test_splits_long_sentence_on_words():
    words = " ".join([f"w{i}" for i in range(80)])
    out = split_text_for_segmented_tts(words, max_chars=40)
    assert len(out) >= 2
    assert "".join(out).replace(" ", "") == words.replace(" ", "")


def test_multiple_sentences_split_when_needed():
    """max_chars is clamped to >=40 in split_text_for_segmented_tts (matches Piper TTS)."""
    s1 = "This is sentence one."
    s2 = "This is sentence two with extra words."
    text = f"{s1} {s2}"
    out = split_text_for_segmented_tts(text, max_chars=40)
    assert len(out) >= 2
    assert all(len(c) <= 40 for c in out)
    joined = " ".join(out)
    assert "sentence one" in joined
    assert "sentence two" in joined
