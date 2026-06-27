"""Layer 32 — grounded answer from retrieved course chunks via Groq.

Groq mocked at the httpx boundary. The prompt carries only the retrieved chunks,
the sources are the distinct documents behind them, a timeout becomes a clean
error (not a crash), and the GROQ key never appears in the request log.
"""

import httpx

from app.rag.answer import answer_question

CHUNKS = [
    {"chunk_text": "Late work loses 10% per day.", "source_title": "Syllabus",
     "source_url": "https://s.test/courses/7/assignments/syllabus", "rank": 0.9},
    {"chunk_text": "Late work loses 10% per day.", "source_title": "Syllabus",
     "source_url": "https://s.test/courses/7/assignments/syllabus", "rank": 0.8},
    {"chunk_text": "Final project is due week 15.", "source_title": "Final Project",
     "source_url": "https://s.test/courses/7/assignments/3", "rank": 0.5},
]


def groq_client(answer_text, capture=None):
    def handler(request):
        if capture is not None:
            capture["auth"] = request.headers.get("Authorization", "")
            capture["body"] = request.content.decode()
        return httpx.Response(200, json={
            "choices": [{"message": {"content": answer_text}}],
        })
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_answer_uses_chunks_and_returns_distinct_sources(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "secret-key")
    capture = {}
    client = groq_client("Late work loses 10% per day.", capture)

    result = answer_question("What is the late policy?", CHUNKS, client)

    assert "10%" in result["answer"]
    # Distinct documents, in retrieval order: Syllabus then Final Project.
    assert result["sources"] == [
        {"title": "Syllabus", "url": "https://s.test/courses/7/assignments/syllabus"},
        {"title": "Final Project", "url": "https://s.test/courses/7/assignments/3"},
    ]
    # The chunk text is in the prompt; the key is only in the header, not logged.
    assert "Late work loses 10%" in capture["body"]
    assert "secret-key" not in capture["body"]


def test_timeout_returns_clean_error(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "secret-key")

    def handler(request):
        raise httpx.TimeoutException("too slow")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = answer_question("anything", CHUNKS, client)
    assert result["error"] == "timeout"
    assert "took too long" in result["answer"].lower()


def test_no_chunks_short_circuits_to_refusal(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "secret-key")
    # With no retrieved context we don't even call Groq; we refuse directly.
    client = httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(500)))
    result = answer_question("anything", [], client)
    assert result["answer"] == "I don't know based on the provided course documents."
    assert result["sources"] == []
