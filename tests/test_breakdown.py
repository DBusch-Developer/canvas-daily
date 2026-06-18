"""Layer 12 - redesigned AI breakdown: structured JSON sections + modal cards.

Groq is mocked at the httpx transport boundary (no network). These tests pin the
new contract: the model is asked for a JSON object with four named sections, the
response is parsed into those fields, bad JSON degrades to a clean error, and the
detail page renders the trigger at the top with a dialog while the result renders
as section cards.
"""

import json
import logging
from datetime import datetime

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.ai import (
    AIError,
    AITimeoutError,
    GROQ_MODEL,
    SECTION_KEYS,
    build_section_messages,
    generate_sections,
)
from app.models import Assignment, Connection, User
from app.web import create_app, get_groq_client, get_session

FULL_ASSIGNMENT = {
    "title": "Industrial Revolution essay",
    "description": "Argue one cause mattered most.",
    "points": 100,
    "due_date": "2026-06-25",
    "course": "History 101",
}

SECTIONS_JSON = json.dumps({
    "whats_being_asked": "Pick one cause and argue it mattered most.",
    "where_to_research": "- Library database for peer-reviewed history journals\n- Search terms: industrialization causes",
    "outline": "- Intro with thesis\n- Body: three supporting points\n- Conclusion",
    "ideas": "- Angle: economic vs social causes",
})


def client_for(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def groq_json(content):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


# ---- ai.py: prompt, request shape, parsing, errors ----

def test_section_prompt_names_the_four_json_keys():
    system = build_section_messages(FULL_ASSIGNMENT)[0]["content"]
    assert "JSON" in system
    for key in ("whats_being_asked", "where_to_research", "outline", "ideas"):
        assert key in system
    # The research guard: never invent sources.
    assert "invent" in system.lower()


def test_context_block_drops_missing_fields():
    sparse = {"title": "Reading log", "points": 10}
    user_content = build_section_messages(sparse)[-1]["content"]
    assert "Reading log" in user_content
    assert "Description" not in user_content
    assert "Course" not in user_content
    assert "None" not in user_content


def test_sends_json_request_and_parses_sections():
    captured = []

    def handler(request):
        captured.append(request)
        return groq_json(SECTIONS_JSON)

    sections = generate_sections(FULL_ASSIGNMENT, client_for(handler), "secret-key")

    assert set(sections.keys()) == set(SECTION_KEYS)
    assert sections["whats_being_asked"] == "Pick one cause and argue it mattered most."

    payload = json.loads(captured[0].read().decode())
    assert payload["model"] == GROQ_MODEL
    assert payload["temperature"] == 0.5
    assert payload["response_format"] == {"type": "json_object"}
    assert captured[0].headers["Authorization"] == "Bearer secret-key"


def test_missing_keys_default_to_empty_string():
    partial = json.dumps({"whats_being_asked": "Just this one."})
    sections = generate_sections(FULL_ASSIGNMENT, client_for(lambda r: groq_json(partial)), "k")
    assert sections["whats_being_asked"] == "Just this one."
    assert sections["outline"] == ""
    assert sections["ideas"] == ""


def test_invalid_json_raises_clean_ai_error():
    handler = lambda r: groq_json("Sorry, here is some prose, not JSON.")
    with pytest.raises(AIError):
        generate_sections(FULL_ASSIGNMENT, client_for(handler), "k")


def test_timeout_raises_ai_timeout_error():
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)
    with pytest.raises(AITimeoutError):
        generate_sections(FULL_ASSIGNMENT, client_for(handler), "k")


def test_http_error_raises_ai_error():
    handler = lambda r: httpx.Response(500, json={"error": "upstream boom"})
    with pytest.raises(AIError):
        generate_sections(FULL_ASSIGNMENT, client_for(handler), "k")


def test_api_key_never_appears_in_logs(caplog):
    key = "super-secret-key-do-not-leak"

    def handler(request):
        raise httpx.ConnectError("down", request=request)

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(AIError):
            generate_sections(FULL_ASSIGNMENT, client_for(handler), key)
    assert key not in caplog.text


# ---- web.py + templates: cards, dialog, fragment, error ----

@pytest.fixture
def engine():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    SQLModel.metadata.create_all(eng)
    yield eng
    SQLModel.metadata.drop_all(eng)


@pytest.fixture
def app(engine):
    application = create_app()

    def _get_session():
        with Session(engine) as s:
            yield s

    application.dependency_overrides[get_session] = _get_session
    return application


@pytest.fixture
def client(app):
    return TestClient(app)


def seed_logged_in_assignment(client, engine, email="bd@x.com"):
    client.post("/signup", data={"email": email, "password": "hunter2pw"},
                follow_redirects=False)
    with Session(engine) as s:
        user = s.exec(select(User).where(User.email == email)).one()
        conn = Connection(user_id=user.id, label="Mine", base_url="https://school.test",
                          account_type="student", access_token="canvas-tok")
        s.add(conn)
        s.commit()
        s.refresh(conn)
        a = Assignment(connection_id=conn.id, canvas_assignment_id=1, name="Essay",
                       description="<p>Write it.</p>",
                       due_at=datetime(2026, 6, 25, 12, 0), points_possible=100.0,
                       submission_types=["online_upload"], html_url="https://school.test/a/1",
                       workflow_state="unsubmitted")
        s.add(a)
        s.commit()
        s.refresh(a)
        return a.id


def mock_groq(app, handler):
    app.dependency_overrides[get_groq_client] = lambda: client_for(handler)


def is_full_page(text):
    return "<!doctype" in text.lower() or 'class="topbar"' in text


def test_detail_page_has_top_trigger_and_dialog(client, engine):
    assignment_id = seed_logged_in_assignment(client, engine)
    resp = client.get(f"/assignments/{assignment_id}")
    assert resp.status_code == 200
    assert 'id="breakdown-dialog"' in resp.text
    assert "Break this down with AI" in resp.text
    # Old bottom CTA + result container are gone.
    assert 'class="breakdown-cta"' not in resp.text
    assert 'id="breakdown-result"' not in resp.text


def test_breakdown_renders_section_cards(client, app, engine):
    assignment_id = seed_logged_in_assignment(client, engine)
    mock_groq(app, lambda r: groq_json(SECTIONS_JSON))

    resp = client.post(f"/assignments/{assignment_id}/breakdown")

    assert resp.status_code == 200
    assert "data-breakdown" in resp.text
    assert "Where to start researching" in resp.text
    assert "Outline of the work" in resp.text
    assert "Pick one cause" in resp.text


def test_htmx_request_returns_card_fragment(client, app, engine):
    assignment_id = seed_logged_in_assignment(client, engine)
    mock_groq(app, lambda r: groq_json(SECTIONS_JSON))

    resp = client.post(f"/assignments/{assignment_id}/breakdown",
                       headers={"HX-Request": "true"})

    assert resp.status_code == 200
    assert "data-breakdown" in resp.text
    assert not is_full_page(resp.text)


def test_breakdown_error_renders_notice(client, app, engine):
    assignment_id = seed_logged_in_assignment(client, engine)
    mock_groq(app, lambda r: httpx.Response(500, json={"error": "upstream boom"}))

    resp = client.post(f"/assignments/{assignment_id}/breakdown",
                       headers={"HX-Request": "true"})

    assert resp.status_code == 502
    assert "unavailable" in resp.text.lower()
    assert "upstream boom" not in resp.text
