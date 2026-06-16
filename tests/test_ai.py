import logging

import httpx
import pytest
from fastapi.testclient import TestClient

from app.ai import (
    AIError,
    AITimeoutError,
    GROQ_MODEL,
    build_messages,
    create_app,
    generate_breakdown,
    get_api_key,
    get_groq_client,
)

FULL_ASSIGNMENT = {
    "title": "Persuasive Essay",
    "description": "Argue for or against year-round school.",
    "points": 100,
    "due_date": "2026-06-20",
    "course": "English 9",
}


def client_for(handler):
    """An httpx client answered in-process — no network, no real Groq call."""
    return httpx.Client(transport=httpx.MockTransport(handler))


def groq_reply(content):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def test_context_block_includes_all_present_fields():
    messages = build_messages(FULL_ASSIGNMENT)
    user_content = messages[-1]["content"]

    assert "Persuasive Essay" in user_content
    assert "Argue for or against year-round school." in user_content
    assert "100" in user_content
    assert "2026-06-20" in user_content
    assert "English 9" in user_content


def test_context_block_drops_missing_fields_not_sent_blank():
    sparse = {"title": "Reading log", "points": 10}
    user_content = build_messages(sparse)[-1]["content"]

    assert "Reading log" in user_content
    assert "10" in user_content
    # Absent fields leave no label and no blank/None placeholder.
    assert "Description" not in user_content
    assert "Course" not in user_content
    assert "Due" not in user_content
    assert "None" not in user_content


def test_sends_system_prompt_and_returns_markdown():
    captured = []
    markdown = "## What's being asked\nWrite an essay.\n## Time estimate\n2 hours"

    def handler(request):
        captured.append(request)
        return groq_reply(markdown)

    result = generate_breakdown(FULL_ASSIGNMENT, client_for(handler), "secret-key")

    assert result == markdown

    body = captured[0].read().decode()
    import json
    payload = json.loads(body)
    assert payload["model"] == GROQ_MODEL
    assert payload["temperature"] == 0.5
    system = payload["messages"][0]
    assert system["role"] == "system"
    for section in ("What's being asked", "Step-by-step plan", "Watch out for", "Time estimate"):
        assert section in system["content"]
    # Key rides in the header, never the URL.
    assert captured[0].headers["Authorization"] == "Bearer secret-key"


def test_timeout_raises_ai_timeout_error():
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(AITimeoutError):
        generate_breakdown(FULL_ASSIGNMENT, client_for(handler), "k")


def test_other_failure_raises_clean_ai_error():
    def handler(request):
        return httpx.Response(500, json={"error": "upstream boom"})

    with pytest.raises(AIError):
        generate_breakdown(FULL_ASSIGNMENT, client_for(handler), "k")


def test_api_key_never_appears_in_logs(caplog):
    key = "super-secret-key-do-not-leak"

    def handler(request):
        raise httpx.ConnectError("down", request=request)

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(AIError):
            generate_breakdown(FULL_ASSIGNMENT, client_for(handler), key)

    assert key not in caplog.text


# --- HTTP endpoint: the 504 / clean-error mapping lives here ---

def app_with_handler(handler):
    app = create_app()
    app.dependency_overrides[get_groq_client] = lambda: client_for(handler)
    app.dependency_overrides[get_api_key] = lambda: "test-key"
    return TestClient(app, raise_server_exceptions=False)


def test_endpoint_timeout_returns_504():
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    resp = app_with_handler(handler).post("/breakdown", json=FULL_ASSIGNMENT)

    assert resp.status_code == 504
    assert "too long" in resp.json()["error"].lower()


def test_endpoint_other_failure_returns_clean_error_not_500():
    def handler(request):
        return httpx.Response(500, json={"error": "upstream boom"})

    resp = app_with_handler(handler).post("/breakdown", json=FULL_ASSIGNMENT)

    assert resp.status_code == 502
    assert resp.json()["error"]
    assert "upstream boom" not in resp.text  # upstream detail not leaked
