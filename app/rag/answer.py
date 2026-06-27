"""Generate a grounded answer from retrieved course chunks via Groq.

Groq is the only model (no embeddings anywhere in this feature). The prompt
carries only the retrieved chunks; the model must answer from them or return the
fixed refusal line. Failures become clean messages, never a broken page. The key
rides in the Authorization header and is never logged.
"""

import logging
import os

import httpx

logger = logging.getLogger(__name__)

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"
TEMPERATURE = 0.1
REFUSAL = "I don't know based on the provided course documents."

SYSTEM_PROMPT = (
    "You answer a student's question using ONLY the provided course documents. "
    "If the answer is not in them, reply with exactly this and nothing else:\n"
    f"{REFUSAL}\n"
    "Never invent citations, source titles, or URLs. Be concise and direct."
)


def _distinct_sources(chunks):
    seen, sources = set(), []
    for c in chunks:
        key = (c["source_title"], c["source_url"])
        if key not in seen:
            seen.add(key)
            sources.append({"title": c["source_title"], "url": c["source_url"]})
    return sources


def _context(chunks):
    return "\n\n".join(
        f"[{c['source_title']}]\n{c['chunk_text']}" for c in chunks
    )


def answer_question(question, chunks, client):
    if not chunks:
        return {"answer": REFUSAL, "sources": []}

    sources = _distinct_sources(chunks)
    user_prompt = (
        f"Course documents:\n{_context(chunks)}\n\n"
        f"Question: {question}"
    )
    payload = {
        "model": GROQ_MODEL,
        "temperature": TEMPERATURE,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    }

    try:
        headers = {"Authorization": f"Bearer {os.environ.get('GROQ_API_KEY', '')}"}
        resp = client.post(GROQ_URL, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
    except httpx.TimeoutException:
        return {"answer": "The answer took too long to generate. Please try again.",
                "sources": sources, "error": "timeout"}
    except Exception:
        logger.warning("Groq request failed")
        return {"answer": "Something went wrong generating the answer. Please try again.",
                "sources": sources, "error": "failed"}

    answer = resp.json()["choices"][0]["message"]["content"].strip()
    return {"answer": answer, "sources": sources}
