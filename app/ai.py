"""On-demand AI study breakdown via Groq (OpenAI-compatible API).

Groq is mocked in tests at the httpx transport boundary, so the client is
injected. The assignment context is assembled with missing fields dropped, the
model is asked for a fixed four-section markdown plan, and failures are turned
into clean errors — a timeout into 504, anything else into a clear message.
The API key rides in the Authorization header and is never logged.
"""

import logging
import os

import httpx
from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
GROQ_MODEL = "llama-3.3-70b-versatile"
TEMPERATURE = 0.5

SYSTEM_PROMPT = (
    "You are a study coach helping a student tackle one assignment. "
    "Respond in markdown using exactly these four sections, in order:\n"
    "## What's being asked\n"
    "## Step-by-step plan\n"
    "## Watch out for\n"
    "## Time estimate\n"
    "Make the plan concrete and specific, flag the grading gotchas under "
    "'Watch out for', and phase the time estimate. Keep the whole response "
    "under 300 words. Be direct."
)

# (assignment key, label) in the order they appear in the context block.
_CONTEXT_FIELDS = [
    ("title", "Title"),
    ("course", "Course"),
    ("points", "Points"),
    ("due_date", "Due"),
    ("description", "Description"),
]


class AIError(Exception):
    """The breakdown could not be generated (clean, user-facing failure)."""


class AITimeoutError(AIError):
    """Groq took too long to respond."""


def build_messages(assignment):
    """System prompt plus a user context block; missing fields are omitted."""
    lines = []
    for key, label in _CONTEXT_FIELDS:
        value = assignment.get(key)
        if value is None or value == "":
            continue
        lines.append(f"{label}: {value}")
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "\n".join(lines)},
    ]


def generate_breakdown(assignment, client, api_key):
    """Call Groq and return the markdown breakdown, or raise a clean AIError."""
    try:
        response = client.post(
            f"{GROQ_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": GROQ_MODEL,
                "temperature": TEMPERATURE,
                "messages": build_messages(assignment),
            },
        )
        response.raise_for_status()
    except httpx.TimeoutException as exc:
        logger.warning("AI breakdown timed out: %s", exc.__class__.__name__)
        raise AITimeoutError("The AI breakdown took too long.") from exc
    except httpx.HTTPError as exc:
        logger.warning("AI breakdown failed: %s", exc.__class__.__name__)
        raise AIError("The AI breakdown could not be generated.") from exc

    return response.json()["choices"][0]["message"]["content"]


class BreakdownRequest(BaseModel):
    title: str | None = None
    description: str | None = None
    points: float | None = None
    due_date: str | None = None
    course: str | None = None


def get_groq_client():
    client = httpx.Client(timeout=30.0)
    try:
        yield client
    finally:
        client.close()


def get_api_key():
    return os.environ.get("GROQ_API_KEY", "")


def create_app():
    app = FastAPI()

    @app.post("/breakdown")
    def breakdown(
        req: BreakdownRequest,
        client=Depends(get_groq_client),
        api_key=Depends(get_api_key),
    ):
        try:
            markdown = generate_breakdown(req.model_dump(), client, api_key)
        except AITimeoutError:
            return JSONResponse(
                status_code=504,
                content={"error": "The AI breakdown took too long. Please try again."},
            )
        except AIError:
            return JSONResponse(
                status_code=502,
                content={"error": "The AI breakdown is unavailable right now."},
            )
        return {"markdown": markdown}

    return app
