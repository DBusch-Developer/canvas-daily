# AI Breakdown Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign the "Break this down with AI" feature: trigger at the top of the detail page, results in a modal as styled section cards (not raw markdown), a copy-to-clipboard button, and richer four-section content driven by structured JSON from Groq.

**Architecture:** Groq returns a JSON object with four named string fields. `app/ai.py` parses it; `app/web.py` passes the parsed dict to the template; `breakdown_result.html` renders four cards (model text in auto-escaped Jinja nodes — no raw HTML, so the sanitize rule is satisfied with no markdown library). A native `<dialog>` plus a small `breakdown.js` handle open/close, loading state, and copy. Without JS the same route returns the full breakdown page — one server code path.

**Tech Stack:** FastAPI, SQLModel, Jinja2, httpx (Groq via OpenAI-compatible API, mocked in tests at the transport boundary), HTMX (already loaded), pytest.

## Global Constraints

- TDD-first, red before green. New behavior lives in its own layer `tests/test_breakdown.py` (label `breakdown`).
- Test evidence: `docs/test-evidence/breakdown-red.png` and `breakdown-green.png`, both referenced in README as **Layer 12**. Red captured live, before any implementation. Both PNGs are committed together in the final green commit (matches the existing Layer 11 pattern).
- Groq key from `GROQ_API_KEY` env var; never logged, never in a commit. Key rides in the `Authorization` header.
- AI is on-demand (fires on button press), temperature 0.5, model `llama-3.3-70b-versatile`, base URL `https://api.groq.com/openai/v1`.
- Combined response capped under 500 words.
- Research section prompt must forbid inventing citations, source titles, authors, or URLs.
- Sanitize rule: no raw Canvas/model HTML to the page — model text is rendered only through auto-escaped Jinja text nodes.
- One code path for the breakdown route: `HX-Request` header → fragment, otherwise full page.
- Commit with the pre-commit hook (evidence check + full suite). Short commit messages. Commit on `main`, no branches.
- Run pytest via the venv: `.venv/Scripts/python.exe -m pytest ...`.

---

## File Structure

- `tests/test_breakdown.py` — **create.** Layer 12 tests: ai.py JSON generation + parsing/errors, and route/template rendering of cards + dialog.
- `app/ai.py` — **modify.** Add `SECTIONS_SYSTEM_PROMPT`, `SECTION_KEYS`, `build_section_messages`, `generate_sections`; factor a shared `_request_completion` helper (leave `generate_breakdown` behavior identical so the `ai` layer stays green).
- `app/web.py` — **modify.** Breakdown route calls `generate_sections`, passes `sections` dict to the template.
- `app/templates/detail.html` — **modify.** Move trigger button to the top `brief` section beside "Open in Canvas"; remove bottom CTA + `#breakdown-result`; add `<dialog>` shell.
- `app/templates/breakdown_result.html` — **modify.** Render four section cards from `sections`, or the error notice.
- `app/templates/breakdown.html` — **no change needed** (already includes `breakdown_result.html`); verify it renders cards on the no-JS full page.
- `app/static/app.css` — **modify.** Add modal + section-card styles.
- `app/static/breakdown.js` — **create.** Open/close dialog, loading state, copy-to-clipboard.
- `app/templates/base.html` — **modify.** Load `breakdown.js`.
- `tests/test_htmx.py` — **modify (maintenance).** Swap the mocked markdown reply for the new JSON shape so the htmx layer stays green. No new behavior, no screenshot changes.
- `tests/test_e2e.py` — **modify (maintenance).** Same: swap mocked reply to JSON.
- `CLAUDE.md` — **modify.** Amend the four-section hard rule (new section names, JSON/modal, 500-word cap).
- `README.md` — **modify.** Add Layer 12 to the Test evidence list.

---

## Task 1: Write the failing Layer 12 tests and capture RED

**Files:**
- Create: `tests/test_breakdown.py`
- Capture: `docs/test-evidence/breakdown-red.png`
- Modify: `README.md` (add Layer 12 section with the red image)

**Interfaces:**
- Consumes (does not exist yet — these tests define them): `app.ai.build_section_messages(assignment) -> list[dict]`, `app.ai.generate_sections(assignment, client, api_key) -> dict`, `app.ai.SECTION_KEYS`.
- Produces: the enforced `breakdown` layer.

- [ ] **Step 1: Write `tests/test_breakdown.py`**

```python
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
```

- [ ] **Step 2: Run the layer to confirm it is RED**

Run: `.venv/Scripts/python.exe tools/run_to_html.py breakdown-red tests/test_breakdown.py`
Expected: harness prints `[RED ...]` and writes `docs/test-evidence/breakdown-red.html`. Failures are import errors (`cannot import name 'SECTION_KEYS' / 'build_section_messages' / 'generate_sections'`) and missing template markup.

- [ ] **Step 3: Screenshot the red page**

Serve the folder (file:// is blocked): `.venv/Scripts/python.exe -m http.server 8731 --directory docs/test-evidence` (run in background). Navigate the browser to `http://127.0.0.1:8731/breakdown-red.html`, screenshot the `.frame` element to `breakdown-red.png`, move it into `docs/test-evidence/`. Stop the server. Verify by eye: red FAILED lines legible, no black-on-black.

- [ ] **Step 4: Add the Layer 12 section to the README (red image only for now)**

Insert after the Layer 11 block in the "Test evidence" list:

```markdown
### Layer 12 — Redesigned AI breakdown (structured sections + modal)

The breakdown trigger moves to the top of the detail page; Groq returns a JSON
object with four sections (What's being asked / Where to start researching /
Outline of the work / Ideas & angles) rendered as cards in a modal, with a
copy-to-clipboard button. Bad JSON degrades to a clean error.

![Layer 12 red](docs/test-evidence/breakdown-red.png)
*Red: the structured-section functions and modal markup don't exist yet.*

![Layer 12 green](docs/test-evidence/breakdown-green.png)
*Green: JSON parsed into four sections, cards and dialog rendered, errors clean.*
```

(The green image file does not exist yet — it is captured in Task 4. That's fine; the link is added now and the PNG lands before commit.)

- [ ] **Step 5: Do NOT commit yet.** Red and green PNGs are committed together at the end (Task 6), matching the Layer 11 pattern. The pre-commit hook runs the full suite, which is still red until the implementation lands.

---

## Task 2: Structured generation in `app/ai.py`

**Files:**
- Modify: `app/ai.py`
- Test: `tests/test_breakdown.py` (the ai.py tests from Task 1)

**Interfaces:**
- Produces: `SECTION_KEYS: tuple[str, ...]` = `("whats_being_asked", "where_to_research", "outline", "ideas")`; `build_section_messages(assignment) -> list[dict]`; `generate_sections(assignment, client, api_key) -> dict[str, str]` (raises `AITimeoutError` / `AIError`).
- Consumes: existing `GROQ_BASE_URL`, `GROQ_MODEL`, `TEMPERATURE`, `_CONTEXT_FIELDS`, `AIError`, `AITimeoutError`, `logger`.

- [ ] **Step 1: Add `import json` and the new prompt/keys to `app/ai.py`**

At the top with the other imports add `import json`. After the existing `SYSTEM_PROMPT` block add:

```python
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
```

- [ ] **Step 2: Factor a shared request helper and add `build_section_messages` + `generate_sections`**

Replace the body of `generate_breakdown` so both functions share one POST/error path, and add the new functions. Replace the existing `generate_breakdown` definition with:

```python
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
```

(`build_messages` is unchanged — `_CONTEXT_FIELDS` field-dropping is duplicated in `build_section_messages` deliberately to keep the two prompts independent and the existing `ai` layer untouched.)

- [ ] **Step 3: Run the ai.py tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_breakdown.py -k "not detail and not renders and not htmx and not notice" -v`
Expected: the 8 ai-level tests PASS. (Route/template tests still fail until Task 3.)

- [ ] **Step 4: Confirm the existing `ai` layer is still green**

Run: `.venv/Scripts/python.exe -m pytest tests/test_ai.py -v`
Expected: all PASS (behavior of `generate_breakdown` is unchanged).

---

## Task 3: Route + templates render cards and dialog

**Files:**
- Modify: `app/web.py:249-286` (breakdown route), `app/web.py:21` (import)
- Modify: `app/templates/detail.html`
- Modify: `app/templates/breakdown_result.html`
- Test: `tests/test_breakdown.py` (route/template tests)

**Interfaces:**
- Consumes: `app.ai.generate_sections`, `AIError`, `AITimeoutError`.
- Produces: template context key `sections` (a `dict[str, str]`) on success, or `error` (a `str`) on failure.

- [ ] **Step 1: Point the route at `generate_sections`**

In `app/web.py`, change the import on line 21 from:

```python
from app.ai import AIError, AITimeoutError, generate_breakdown
```
to:
```python
from app.ai import AIError, AITimeoutError, generate_sections
```

Then in the `breakdown` route replace the `try/except`/return block (currently lines ~271-286) with:

```python
        try:
            sections = generate_sections(context, client, os.environ.get("GROQ_API_KEY", ""))
        except AITimeoutError:
            return TEMPLATES.TemplateResponse(
                request, template,
                {"a": assignment, "error": "The AI breakdown took too long. Please try again."},
                status_code=504,
            )
        except AIError:
            return TEMPLATES.TemplateResponse(
                request, template,
                {"a": assignment, "error": "The AI breakdown is unavailable right now."},
                status_code=502,
            )
        return TEMPLATES.TemplateResponse(
            request, template, {"a": assignment, "sections": sections})
```

(The `context` dict and the `template` HX-Request branch above it are unchanged.)

- [ ] **Step 2: Move the trigger to the top and add the dialog in `app/templates/detail.html`**

Replace the `brief` section's action block so the AI button sits beside "Open in Canvas". Change the `<section class="brief">` block to:

```html
  <section class="brief">
    <p class="eyebrow">Mission brief</p>
    <h1 class="brief__title">{{ a.name }}</h1>
    <div class="brief__tags">
      <span class="course-pill course-pill--lg">{{ a.connection.label }}</span>
      <span class="badge badge--{{ tone }}">{{ status }}</span>
    </div>
    <form class="brief__actions" method="post" action="/assignments/{{ a.id }}/breakdown">
      {% if a.html_url %}
        <a class="btn btn--primary" href="{{ a.html_url }}" target="_blank" rel="noopener">Open in Canvas ↗</a>
      {% endif %}
      <button class="btn btn--accent" type="submit"
              id="breakdown-trigger"
              hx-post="/assignments/{{ a.id }}/breakdown"
              hx-target="#breakdown-body"
              hx-swap="innerHTML">✦ Break this down with AI</button>
    </form>
  </section>
```

Then delete the old bottom block (the `<form class="breakdown-cta" ...>` ... `</form>` and the `<div id="breakdown-result" ...></div>`) and replace it with the dialog shell at the end of the `{% block body %}`:

```html
  <dialog id="breakdown-dialog" class="modal">
    <div class="modal__head">
      <p class="modal__eyebrow">✦ AI study breakdown</p>
      <div class="modal__actions">
        <button type="button" class="btn btn--ghost btn--sm" data-copy>Copy</button>
        <button type="button" class="modal__close" data-close aria-label="Close">✕</button>
      </div>
    </div>
    <div id="breakdown-body" class="modal__body"></div>
  </dialog>
```

- [ ] **Step 3: Render section cards in `app/templates/breakdown_result.html`**

Replace the whole file with:

```html
{# The breakdown result only: four section cards, or an error/timeout notice.
   Shared by the full breakdown.html page and the HTMX in-place swap into the
   dialog, so both render identical markup. Model text lands only in
   auto-escaped Jinja text nodes - no raw HTML reaches the page. #}
{% if error %}
  <p class="notice">{{ error }}</p>
{% else %}
  <div class="breakdown-cards" data-breakdown>
    {% for key, title in [
        ('whats_being_asked', "What's being asked"),
        ('where_to_research', 'Where to start researching'),
        ('outline', 'Outline of the work'),
        ('ideas', 'Ideas & angles')] %}
      {% set value = sections[key] %}
      {% if value %}
        <section class="section-card">
          <h3 class="section-card__title">{{ title }}</h3>
          <div class="section-card__body">
            {% for raw in value.split('\n') %}
              {% set line = raw.strip().lstrip('-').strip() %}
              {% if line %}<p>{{ line }}</p>{% endif %}
            {% endfor %}
          </div>
        </section>
      {% endif %}
    {% endfor %}
  </div>
{% endif %}
```

- [ ] **Step 4: Run the route/template tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_breakdown.py -v`
Expected: all tests PASS (ai.py + route/template).

---

## Task 4: Capture GREEN for Layer 12

**Files:**
- Capture: `docs/test-evidence/breakdown-green.png`

- [ ] **Step 1: Render the green page**

Run: `.venv/Scripts/python.exe tools/run_to_html.py breakdown-green tests/test_breakdown.py`
Expected: harness prints `[GREEN (all passed)]` and writes `docs/test-evidence/breakdown-green.html`.

- [ ] **Step 2: Screenshot the green page**

Serve: `.venv/Scripts/python.exe -m http.server 8731 --directory docs/test-evidence` (background). Navigate to `http://127.0.0.1:8731/breakdown-green.html`, screenshot the `.frame` element to `breakdown-green.png`, move into `docs/test-evidence/`. Stop the server. Verify by eye: all green PASSED, every line legible.

(The README already links `breakdown-green.png` from Task 1 Step 4 — no further README edit needed here.)

---

## Task 5: Modal styling and behavior (CSS + JS)

**Files:**
- Modify: `app/static/app.css`
- Create: `app/static/breakdown.js`
- Modify: `app/templates/base.html`

**Interfaces:**
- Consumes DOM ids/attrs produced in Task 3: `#breakdown-dialog`, `#breakdown-trigger`, `#breakdown-body`, `[data-copy]`, `[data-close]`, `[data-breakdown]`, `.section-card`.

- [ ] **Step 1: Add modal + card styles to `app/static/app.css`**

Append after the existing `/* ---- Breakdown ---- */` block (after the `.notice` rule, before `/* ---- Settings ---- */`):

```css
/* ---- Breakdown modal ------------------------------------------------- */

.brief__actions { display: flex; flex-wrap: wrap; gap: .7rem; margin: 0; }

.modal {
  width: min(640px, 92vw);
  max-height: 85vh;
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow);
  padding: 0;
  color: var(--ink);
  background: var(--surface);
  overflow: hidden;
}
.modal::backdrop { background: rgba(16, 24, 40, .45); }
.modal[open] { display: flex; flex-direction: column; }

.modal__head {
  display: flex; align-items: center; justify-content: space-between;
  gap: 1rem; padding: 16px 20px; border-bottom: 1px solid var(--border);
}
.modal__eyebrow { margin: 0; font-weight: 600; color: var(--navy); }
.modal__actions { display: flex; align-items: center; gap: .5rem; }
.modal__close {
  border: 0; background: transparent; cursor: pointer;
  font-size: 1.1rem; line-height: 1; color: var(--muted); padding: .2rem;
}
.modal__close:hover { color: var(--ink); }

.modal__body { padding: 20px; overflow-y: auto; }
.modal__loading { display: flex; align-items: center; gap: .6rem; color: var(--muted); }

.breakdown-cards { display: grid; gap: 16px; }
.section-card {
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--bg-2);
  padding: 14px 16px;
}
.section-card__title { margin: 0 0 .5rem; font-size: 1rem; color: var(--navy); }
.section-card__body p { margin: .25rem 0; line-height: 1.6; }
```

(If any variable name above is not defined in `:root`, substitute the nearest existing one — check the top of `app.css`.)

- [ ] **Step 2: Create `app/static/breakdown.js`**

```javascript
// Detail-page modal for the AI breakdown. Progressive enhancement: without this
// script the form still POSTs and returns the full breakdown page.
(function () {
  var dialog = document.getElementById("breakdown-dialog");
  var trigger = document.getElementById("breakdown-trigger");
  var body = document.getElementById("breakdown-body");
  if (!dialog || !trigger || !body || typeof dialog.showModal !== "function") return;

  trigger.addEventListener("click", function () {
    body.innerHTML =
      '<p class="modal__loading"><span class="spinner"></span> Thinking it through…</p>';
    dialog.showModal();
  });

  dialog.querySelectorAll("[data-close]").forEach(function (btn) {
    btn.addEventListener("click", function () { dialog.close(); });
  });
  dialog.addEventListener("click", function (e) {
    if (e.target === dialog) dialog.close(); // click on backdrop
  });

  var copyBtn = dialog.querySelector("[data-copy]");
  if (copyBtn) {
    copyBtn.addEventListener("click", function () {
      var root = body.querySelector("[data-breakdown]");
      if (!root) return;
      var text = "";
      root.querySelectorAll(".section-card").forEach(function (card) {
        text += card.querySelector(".section-card__title").textContent + "\n";
        card.querySelectorAll(".section-card__body p").forEach(function (p) {
          text += "- " + p.textContent + "\n";
        });
        text += "\n";
      });
      navigator.clipboard.writeText(text.trim()).then(function () {
        copyBtn.textContent = "Copied!";
        setTimeout(function () { copyBtn.textContent = "Copy"; }, 1500);
      });
    });
  }
})();
```

- [ ] **Step 3: Load the script in `app/templates/base.html`**

Below the existing htmx script tag (line ~34), add:

```html
  <script src="/static/breakdown.js"></script>
```

- [ ] **Step 4: Manually verify the modal in a browser**

Start the app, open a detail page, click "Break this down with AI": the dialog opens with a spinner, then four cards. Test Copy (paste into a text editor — section titles + dashed bullets), and close via ✕, backdrop, and Esc. CSS/JS are not under pytest; this manual check is the verification.

---

## Task 6: Keep sibling layers green, update docs, commit

**Files:**
- Modify: `tests/test_htmx.py`, `tests/test_e2e.py`
- Modify: `CLAUDE.md`, `README.md` (Test layers line, optional)

**Interfaces:** none new.

- [ ] **Step 1: Update the htmx layer's mock to the JSON shape**

In `tests/test_htmx.py` replace the `MARKDOWN` constant (lines ~20-23) with a JSON reply and update the two assertions that reference `"Measure stuff."`:

```python
import json

SECTIONS = json.dumps({
    "whats_being_asked": "Measure stuff.",
    "where_to_research": "- Lab manual",
    "outline": "- Method\n- Results",
    "ideas": "- Compare runs",
})
```

Then in `test_htmx_success_returns_only_the_fragment` and `test_normal_post_still_returns_full_page`, change the mock reply `content` from `MARKDOWN` to `SECTIONS` and keep `assert "Measure stuff." in resp.text` (it now appears inside the rendered card). The timeout/error tests are unchanged.

- [ ] **Step 2: Update the e2e breakdown test's mock to JSON**

In `tests/test_e2e.py`, `test_breakdown_button_renders_markdown` (lines ~158-176): replace the markdown string with a JSON reply and adjust the assertions:

```python
    import json
    sections = json.dumps({
        "whats_being_asked": "Measure stuff.",
        "where_to_research": "- Lab manual",
        "outline": "- Method",
        "ideas": "- Compare",
    })

    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": sections}}]})
```

Keep `assert "Measure stuff." in resp.text`; change `assert "being asked" in resp.text` to `assert "What's being asked" in resp.text` (the card title). Optionally rename the test to `test_breakdown_button_renders_sections`.

- [ ] **Step 3: Amend the hard rule in `CLAUDE.md`**

Replace the bullet:

```
- **The AI breakdown keeps its four-section format** — What's being asked / Step-by-step plan / Watch out for / Time estimate — capped under 300 words, temperature 0.5.
```
with:
```
- **The AI breakdown keeps its four-section format** — What's being asked / Where to start researching / Outline of the work / Ideas & angles — returned as a JSON object, rendered as cards in a modal, capped under 500 words, temperature 0.5. The research section must never invent citations, source titles, or URLs.
```

- [ ] **Step 4: Run the full suite**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: all tests PASS (69 prior + the new `breakdown` layer; htmx/e2e green against the JSON shape).

- [ ] **Step 5: Run the evidence check**

Run: `.venv/Scripts/python.exe tools/check_evidence.py`
Expected: `OK - evidence present and documented for: ai, autosync, breakdown, canvas, completed, dates, e2e, htmx, mailer, models, setup, sync`.

- [ ] **Step 6: Commit (full pre-commit hook) and push**

```bash
git add app/ tests/ docs/test-evidence/breakdown-red.png docs/test-evidence/breakdown-green.png docs/test-evidence/breakdown-red.html docs/test-evidence/breakdown-green.html README.md CLAUDE.md
git commit -m "Redesign AI breakdown: top trigger, modal cards, structured sections (Layer 12)"
git push origin main
```
Expected: `[pre-commit] ok`, full suite passes, commit lands on `main`, push succeeds.

---

## Self-Review

**Spec coverage:**
- Placement (button to top) → Task 3 Step 2; asserted by `test_detail_page_has_top_trigger_and_dialog`.
- Modal presentation → Task 3 (dialog shell) + Task 5 (CSS/JS).
- No-JS fallback / one code path → route unchanged in HX branching; Task 3 Step 1; `breakdown.html` still serves full page.
- Structured JSON content (4 sections) → Task 2; asserted by `test_sends_json_request_and_parses_sections`, `test_missing_keys_default_to_empty_string`.
- No fabricated sources → prompt guard in Task 2 Step 1; asserted by `test_section_prompt_names_the_four_json_keys`.
- Sanitize rule → cards use auto-escaped Jinja text nodes (Task 3 Step 3); no markdown lib, no nh3 round-trip.
- Copy button → Task 5 Step 2.
- Failure handling (504/502, invalid JSON) → Task 2 + Task 3 Step 1; asserted by `test_invalid_json_raises_clean_ai_error`, `test_timeout_raises_ai_timeout_error`, `test_breakdown_error_renders_notice`.
- 500-word cap, temp 0.5 → prompt + payload in Task 2.
- Rule + docs update → Task 6 Step 3; README Layer 12 → Task 1 Step 4.
- TDD evidence (red live, green) → Task 1 (red) + Task 4 (green); both PNGs committed in Task 6.

**Placeholder scan:** No TBD/TODO; every code step shows full code; commands have expected output.

**Type consistency:** `generate_sections` returns `dict[str, str]` keyed by `SECTION_KEYS` everywhere (ai.py, tests, template iterates the same four keys). `sections` is the template context key in both web.py and `breakdown_result.html`. `build_section_messages` signature matches its test usage.

**Known coupling to verify during execution:** Jinja `value.split('\n')` relies on Jinja interpreting `'\n'` as a newline (it does). The CSS uses `var(--bg-2)`, `var(--navy)`, `var(--ink)`, `var(--muted)`, `var(--surface)`, `var(--border)`, `var(--radius)`, `var(--radius-lg)`, `var(--shadow)` — all already used elsewhere in `app.css`; if any differs, substitute the nearest existing token (noted in Task 5 Step 1).
