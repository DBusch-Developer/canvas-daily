"""Layer 13 - AI breakdown sections as JSON arrays (bullets).

Layer 12 asked Groq for a JSON object whose values were multi-line bulleted
strings. On real, longer assignments the model emitted bare unquoted dash-lines
that aren't valid JSON, so Groq's strict json_object validator returned 400 and
the page showed a 502.

This layer adds `generate_bullets`: it asks Groq for each section as a JSON
*array of strings*, which the model reliably encodes as valid JSON, and returns
each section as a clean list of bullet strings. A stray string value is coerced
into bullets rather than crashing. Groq is mocked at the transport boundary.
"""

import json
import logging

import httpx
import pytest

from app.ai import (
    AIError,
    AITimeoutError,
    GROQ_MODEL,
    SECTION_KEYS,
    build_bullet_messages,
    generate_bullets,
)

FULL_ASSIGNMENT = {
    "title": "Industrial Revolution essay",
    "description": "Argue one cause mattered most.",
    "points": 100,
    "due_date": "2026-06-25",
    "course": "History 101",
}

BULLETS_JSON = json.dumps({
    "whats_being_asked": ["Pick one cause and argue it mattered most."],
    "where_to_research": ["Library database for peer-reviewed history journals",
                          "Search terms: industrialization causes"],
    "outline": ["Intro with thesis", "Body: three supporting points", "Conclusion"],
    "ideas": ["Angle: economic vs social causes"],
})


def client_for(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def groq_json(content):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def test_bullet_prompt_asks_for_json_arrays_of_the_four_keys():
    system = build_bullet_messages(FULL_ASSIGNMENT)[0]["content"]
    assert "JSON" in system
    # Each value must be an ARRAY of strings - that is what keeps Groq's strict
    # json_object validator from rejecting multi-line bulleted string values.
    assert "array" in system.lower()
    for key in SECTION_KEYS:
        assert key in system
    # The research guard carries over: never invent sources.
    assert "invent" in system.lower()


def test_context_block_drops_missing_fields():
    sparse = {"title": "Reading log", "points": 10}
    user_content = build_bullet_messages(sparse)[-1]["content"]
    assert "Reading log" in user_content
    assert "Description" not in user_content
    assert "Course" not in user_content
    assert "None" not in user_content


def test_sends_json_request_and_parses_arrays_into_lists():
    captured = []

    def handler(request):
        captured.append(request)
        return groq_json(BULLETS_JSON)

    sections = generate_bullets(FULL_ASSIGNMENT, client_for(handler), "secret-key")

    assert set(sections.keys()) == set(SECTION_KEYS)
    assert sections["whats_being_asked"] == ["Pick one cause and argue it mattered most."]
    assert sections["outline"] == ["Intro with thesis", "Body: three supporting points", "Conclusion"]

    payload = json.loads(captured[0].read().decode())
    assert payload["model"] == GROQ_MODEL
    assert payload["temperature"] == 0.5
    assert payload["response_format"] == {"type": "json_object"}
    assert captured[0].headers["Authorization"] == "Bearer secret-key"


def test_missing_keys_default_to_empty_list():
    partial = json.dumps({"whats_being_asked": ["Just this one."]})
    sections = generate_bullets(FULL_ASSIGNMENT, client_for(lambda r: groq_json(partial)), "k")
    assert sections["whats_being_asked"] == ["Just this one."]
    assert sections["outline"] == []
    assert sections["ideas"] == []


def test_stray_string_value_is_coerced_into_clean_bullets():
    # If the model returns a multi-line string instead of an array, split it into
    # clean bullets (leading dashes trimmed) rather than crashing or showing dashes.
    raw = json.dumps({
        "whats_being_asked": "- one\n- two",
        "where_to_research": "", "outline": [], "ideas": [],
    })
    sections = generate_bullets(FULL_ASSIGNMENT, client_for(lambda r: groq_json(raw)), "k")
    assert sections["whats_being_asked"] == ["one", "two"]
    assert sections["where_to_research"] == []


def test_invalid_json_raises_clean_ai_error():
    handler = lambda r: groq_json("Sorry, here is some prose, not JSON.")
    with pytest.raises(AIError):
        generate_bullets(FULL_ASSIGNMENT, client_for(handler), "k")


def test_timeout_raises_ai_timeout_error():
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)
    with pytest.raises(AITimeoutError):
        generate_bullets(FULL_ASSIGNMENT, client_for(handler), "k")


def test_http_error_raises_ai_error():
    handler = lambda r: httpx.Response(500, json={"error": "upstream boom"})
    with pytest.raises(AIError):
        generate_bullets(FULL_ASSIGNMENT, client_for(handler), "k")


def test_api_key_never_appears_in_logs(caplog):
    key = "super-secret-key-do-not-leak"

    def handler(request):
        raise httpx.ConnectError("down", request=request)

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(AIError):
            generate_bullets(FULL_ASSIGNMENT, client_for(handler), key)
    assert key not in caplog.text
