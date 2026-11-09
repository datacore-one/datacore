"""No chunk handed to Kokoro may exceed its input limit.

On 2026-09-28 and 2026-09-29 Winston's morning audio failed with
"index 510 is out of bounds for axis 0 with size 510": the briefing writer had
fallen back to a facts-only page whose bullet lines carry no sentence
punctuation, and the chunker only split on . ! ? — so a single "sentence" of
well over 400 characters went to Kokoro whole. Kokoro raised, the code fell
through to the (deliberately disabled) cloud speech path, and no audio went
out. Any text shape must produce chunks within the limit.
"""

import importlib.util
import sys
import types
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parent.parent / "lib" / "speak_brief.py"


@pytest.fixture()
def sb(monkeypatch):
    for name in ("kokoro_onnx", "soundfile", "numpy"):
        if name not in sys.modules:
            monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    spec = importlib.util.spec_from_file_location("speak_brief_chunk_t", LIB)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_unpunctuated_bullet_lines_are_split_within_limit(sb):
    # The shape of the facts-only fallback: many short lines, no full stops.
    para = "\n".join(f"• metric number {i} has value {i * 7} in the latest reading" for i in range(40))
    chunks = sb._chunk_for_kokoro(para)
    assert chunks, "text must produce chunks"
    assert max(len(c) for c in chunks) <= sb.KOKORO_CHUNK_CHARS


def test_one_endless_sentence_is_split_on_words(sb):
    para = " ".join(["word"] * 600)  # ~3000 chars, no punctuation, no newlines
    chunks = sb._chunk_for_kokoro(para)
    assert max(len(c) for c in chunks) <= sb.KOKORO_CHUNK_CHARS
    assert " ".join(chunks).split() == para.split(), "no words lost or reordered"


def test_normal_prose_keeps_sentence_boundaries(sb):
    para = "Good morning. " + " ".join(f"This is sentence {i}." for i in range(60))
    chunks = sb._chunk_for_kokoro(para)
    assert max(len(c) for c in chunks) <= sb.KOKORO_CHUNK_CHARS
    assert all(c.endswith(".") for c in chunks), "prose chunks should end at a sentence"
