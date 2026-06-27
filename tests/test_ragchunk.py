"""Layer 28 — split course text into paragraph chunks for retrieval.

Pure function, no I/O, no mocks: blank-line-separated paragraphs become chunks,
blank runs are dropped, and an over-long paragraph is split to a max size.
"""

from app.rag.chunk import chunk_text


def test_splits_on_blank_lines_and_drops_empties():
    text = "First para.\n\n   \n\nSecond para.\n\nThird."
    assert chunk_text(text) == ["First para.", "Second para.", "Third."]


def test_long_paragraph_is_split_to_max_chars():
    para = " ".join(["word"] * 500)  # ~2500 chars, one paragraph
    chunks = chunk_text(para, max_chars=200)
    assert len(chunks) > 1
    assert all(len(c) <= 200 for c in chunks)
    # No content lost: every word survives across the chunks.
    assert " ".join(chunks).split() == para.split()


def test_empty_text_yields_no_chunks():
    assert chunk_text("") == []
    assert chunk_text("   \n\n  ") == []
