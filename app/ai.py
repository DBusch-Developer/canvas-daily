"""On-demand AI study breakdown via Groq (OpenAI-compatible API).

Groq is mocked in tests at the httpx transport boundary, so the client is
injected. The assignment context is assembled with missing fields dropped, the
model is asked for a fixed four-section markdown plan, and failures are turned
into clean errors — a timeout into 504, anything else into a clear message.
The API key rides in the Authorization header and is never logged.
"""

import json
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

SECTIONS_SYSTEM_PROMPT = (
    "You are a study coach helping a student get started on one assignment. "
    "Respond with a single JSON object and nothing else. It must have exactly "
    "these four string keys, in this order:\n"
    '  "whats_being_asked": restate the task in plain language, grounded only in '
    "the assignment details given.\n"
    '  "where_to_research": concrete research directions - kinds of sources, '
    "search terms, and library databases to try. Never invent specific "
    "citations, source titles, authors, or URLs.\n"
    '  "outline": a skeleton of the finished work - the sections or steps it '
    "should contain.\n"
    '  "ideas": possible approaches, thesis angles, or directions to explore.\n'
    "Within each value, put each point on its own line starting with '- '. Keep "
    "the whole response under 500 words. Be direct and specific."
)

SECTION_KEYS = ("whats_being_asked", "where_to_research", "outline", "ideas")

# Layer 13: ask for arrays of strings, not multi-line bulleted strings. Groq's
# strict json_object validator rejects the multi-line form on longer assignments.
SECTIONS_BULLETS_PROMPT = (
    "You are a study coach helping a student get started on one assignment. "
    "Respond with a single JSON object and nothing else. It must have exactly "
    "these four keys, and each value must be a JSON array of short plain-text "
    "strings - one idea per string, no markdown, no leading dashes:\n"
    '  "whats_being_asked": restate the task in plain language, grounded only in '
    "the assignment details given.\n"
    '  "where_to_research": concrete research directions - kinds of sources, '
    "search terms, and library databases to try. Never invent specific "
    "citations, source titles, authors, or URLs.\n"
    '  "outline": the sections or steps the finished work should contain.\n'
    '  "ideas": possible approaches, thesis angles, or directions to explore.\n'
    "Keep the whole response under 500 words. Be direct and specific."
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


def _request_completion(client, api_key, payload):
    """POST to Groq, map failures to clean errors, return the message content."""
    try:
        response = client.post(
            f"{GROQ_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
        )
        response.raise_for_status()
    except httpx.TimeoutException as exc:
        logger.warning("AI breakdown timed out: %s", exc.__class__.__name__)
        raise AITimeoutError("The AI breakdown took too long.") from exc
    except httpx.HTTPError as exc:
        logger.warning("AI breakdown failed: %s", exc.__class__.__name__)
        raise AIError("The AI breakdown could not be generated.") from exc

    return response.json()["choices"][0]["message"]["content"]


def generate_breakdown(assignment, client, api_key):
    """Call Groq and return the markdown breakdown, or raise a clean AIError."""
    return _request_completion(client, api_key, {
        "model": GROQ_MODEL,
        "temperature": TEMPERATURE,
        "messages": build_messages(assignment),
    })


def build_section_messages(assignment):
    """System prompt asking for JSON sections, plus the same context block."""
    lines = []
    for key, label in _CONTEXT_FIELDS:
        value = assignment.get(key)
        if value is None or value == "":
            continue
        lines.append(f"{label}: {value}")
    return [
        {"role": "system", "content": SECTIONS_SYSTEM_PROMPT},
        {"role": "user", "content": "\n".join(lines)},
    ]


def generate_sections(assignment, client, api_key):
    """Call Groq in JSON mode and return the four sections, or raise AIError."""
    content = _request_completion(client, api_key, {
        "model": GROQ_MODEL,
        "temperature": TEMPERATURE,
        "response_format": {"type": "json_object"},
        "messages": build_section_messages(assignment),
    })
    try:
        data = json.loads(content)
        if not isinstance(data, dict):
            raise ValueError("expected a JSON object")
    except (ValueError, TypeError) as exc:
        logger.warning("AI breakdown returned invalid JSON")
        raise AIError("The AI breakdown could not be generated.") from exc
    return {key: str(data.get(key, "")).strip() for key in SECTION_KEYS}


def build_bullet_messages(assignment):
    """System prompt asking for JSON arrays, plus the same context block."""
    lines = []
    for key, label in _CONTEXT_FIELDS:
        value = assignment.get(key)
        if value is None or value == "":
            continue
        lines.append(f"{label}: {value}")
    return [
        {"role": "system", "content": SECTIONS_BULLETS_PROMPT},
        {"role": "user", "content": "\n".join(lines)},
    ]


def _as_bullets(value):
    """Coerce a section value into a clean list of bullet strings.

    The model is asked for a JSON array of strings, but be defensive: a stray
    multi-line string is split on its lines (with any leading dash trimmed), and
    blanks are dropped. Anything else becomes an empty list.
    """
    if isinstance(value, list):
        items = [str(item).strip() for item in value]
    elif isinstance(value, str):
        items = [line.strip().lstrip("-").strip() for line in value.splitlines()]
    else:
        items = []
    return [item for item in items if item]


def generate_bullets(assignment, client, api_key):
    """Call Groq in JSON mode and return each section as a list of bullets.

    Asking for JSON arrays (rather than multi-line bulleted strings) keeps Groq's
    strict json_object validator from rejecting the response with a 400.
    """
    content = _request_completion(client, api_key, {
        "model": GROQ_MODEL,
        "temperature": TEMPERATURE,
        "response_format": {"type": "json_object"},
        "messages": build_bullet_messages(assignment),
    })
    try:
        data = json.loads(content)
        if not isinstance(data, dict):
            raise ValueError("expected a JSON object")
    except (ValueError, TypeError) as exc:
        logger.warning("AI breakdown returned invalid JSON")
        raise AIError("The AI breakdown could not be generated.") from exc
    return {key: _as_bullets(data.get(key)) for key in SECTION_KEYS}


CLASSIFY_SYSTEM_PROMPT = (
    "You decide which Canvas courses are real academic classes a student takes "
    "for a grade, versus extras Canvas exposes that are not real classes — clubs, "
    "honor societies, help desks, lunch/social spaces, orientations, and parent or "
    "student centers.\n"
    "You are given a numbered list of courses, each with its name and whether it "
    "currently has any graded assignments. Judge primarily from the name; the "
    "assignment signal is secondary.\n"
    "Respond with a single JSON array of booleans and nothing else — exactly one "
    "entry per course, in the same order: true if it is a real class, false if it "
    "is an extra."
)


def build_classify_messages(courses):
    """A numbered course list (name + assignment signal) for classification."""
    lines = []
    for i, c in enumerate(courses):
        has = "yes" if c.get("has_assignments") else "no"
        lines.append(f"{i}. {c.get('name', '')} (has assignments: {has})")
    return [
        {"role": "system", "content": CLASSIFY_SYSTEM_PROMPT},
        {"role": "user", "content": "\n".join(lines)},
    ]


def classify_courses(courses, client, api_key):
    """Return a parallel list of is_real booleans for each course.

    `courses` is a list of {"name", "has_assignments"}. Raises AIError /
    AITimeoutError on failure or a malformed response so the caller can fall
    back to showing everything.
    """
    if not courses:
        return []
    content = _request_completion(client, api_key, {
        "model": GROQ_MODEL,
        "temperature": TEMPERATURE,
        "response_format": {"type": "json_object"},
        "messages": build_classify_messages(courses),
    })
    try:
        data = json.loads(content)
        # Accept a bare array, or an object wrapping one (json_object mode).
        if isinstance(data, dict):
            for value in data.values():
                if isinstance(value, list):
                    data = value
                    break
        if (not isinstance(data, list) or len(data) != len(courses)
                or not all(isinstance(x, bool) for x in data)):
            raise ValueError("expected a JSON array of booleans, one per course")
    except (ValueError, TypeError) as exc:
        logger.warning("Course classification returned invalid JSON")
        raise AIError("Course classification could not be completed.") from exc
    return data


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
