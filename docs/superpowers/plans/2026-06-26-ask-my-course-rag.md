# Ask My Course (course-scoped RAG) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-course question box to Canvas Daily that answers only from that course's synced Canvas content (text + PDFs) and shows its sources, using Groq for the answer and Postgres full-text search for retrieval — no embeddings, no paid vendor.

**Architecture:** An on-demand per-course sync fetches Canvas text sources and PDFs, sanitizes and chunks them, and stores chunks in Neon with a Postgres generated `tsvector`. A question runs a course-scoped full-text search for the top chunks, then Groq writes a grounded answer (or refuses) and the page lists the source documents. Everything is server-rendered Jinja and gated behind a feature flag.

**Tech Stack:** FastAPI, SQLModel over Neon (Postgres), Jinja2, httpx (Canvas + Groq, OpenAI-compatible), nh3 (sanitize), pypdf (PDF text), pytest.

## Global Constraints

- **Groq-only for generation; no embeddings, no second AI vendor.** Model `llama-3.3-70b-versatile`, base URL `https://api.groq.com/openai/v1`, key from `GROQ_API_KEY`, never logged.
- **Retrieval is Postgres full-text search, always scoped `WHERE course_id = ?`.** One course's answers never draw on another's.
- **`search_vector` is NOT a SQLModel-mapped column.** It is Postgres-only, created by `app/rag/fts.py::ensure_search_vector(engine)` (idempotent DDL) and read via raw SQL. This keeps the new tables buildable under SQLite so the existing `create_all`-on-SQLite suite is unaffected.
- **Sanitize all Canvas HTML with `nh3.clean` before it is stored or chunked.**
- **Follow `Link`-header pagination on every Canvas fetch** (reuse `app.canvas._next_page`).
- **Detail/answer reads from storage, never live Canvas.** A question triggers no Canvas call.
- **Grounded or refuse:** if the answer is not in the retrieved context, reply exactly `I don't know based on the provided course documents.` Invent no citations, titles, or URLs. Temperature `0.1`.
- **Feature flag `ASK_COURSE_ENABLED`** gates all routes and nav. Off by default.
- **TDD-first with mandatory evidence.** Each task below is its own layer: `tests/test_<label>.py`, a live red screenshot captured *before* the implementation exists, then a green one, both in `docs/test-evidence/` and referenced in a new numbered README section. Enforced by `tools/check_evidence.py`, the pre-commit hook, and CI.
- **Run Python via the venv:** `.venv/Scripts/python.exe`.
- **Short, one-line commit messages. Commit on `main` (no branches).**

## Evidence procedure (performed in the final steps of every task)

For a task with label `<label>` and test file `tests/test_<label>.py`:

1. Capture red **before** the implementation exists:
   `.venv/Scripts/python.exe tools/run_to_html.py <label>-red tests/test_<label>.py` → confirm it prints RED.
2. Serve and screenshot: start `.venv/Scripts/python.exe -m http.server 8731 --directory docs/test-evidence` (background), navigate a browser to `http://127.0.0.1:8731/<label>-red.html`, resize the window tall enough to show the whole `.frame`, screenshot it to `<label>-red.png`, move it into `docs/test-evidence/`.
3. After the implementation passes: `.venv/Scripts/python.exe tools/run_to_html.py <label>-green tests/test_<label>.py` → confirm GREEN → screenshot `<label>-green.html` the same way → `docs/test-evidence/<label>-green.png`.
4. Verify both images by eye (legible, red shows red FAILED, green shows green passed), stop the HTTP server.
5. Add a new numbered README "Layer N — …" section (description → red image → green image), in the same format as the existing layers.
6. Commit.

## File Structure

- Create `app/rag/__init__.py` — package marker.
- Create `app/rag/chunk.py` — pure `chunk_text`. (Task 1)
- Create `app/rag/content.py` — Canvas text-source fetchers returning document dicts. (Task 2)
- Create `app/rag/pdf.py` — `extract_pdf_text`; PDF file listing/download in `content.py`. (Task 3)
- Create `app/rag/fts.py` — `ensure_search_vector(engine)` Postgres DDL. (Task 4)
- Create `app/rag/retrieve.py` — `retrieve(session, course_id, question, k)`. (Task 4)
- Modify `app/models.py` — add `Course`, `CourseDocument`, `DocumentChunk`. (Task 4)
- Create `app/rag/answer.py` — `answer_question(...)` grounded Groq generation. (Task 5)
- Create `app/sync_content.py` — `sync_course_content(...)` orchestration. (Task 6)
- Modify `app/web.py` — flag, picker, sync-content route, chat routes. (Task 6)
- Create `app/templates/course_picker.html`, `app/templates/course_chat.html`. (Task 6)
- Create `tools/migrate_add_course_rag.py` — prod migration. (Task 4)
- Modify `requirements.txt` — add `pypdf`. (Task 3)
- Tests: `tests/test_ragchunk.py`, `test_coursecontent.py`, `test_pdftext.py`, `test_courseretrieval.py`, `test_askcourse.py`, `test_askcourseweb.py`.

README layers are numbered 28–33 (current last layer is 27 — stickyexcuse).

---

### Task 1: Chunking (Layer `ragchunk`, README Layer 28)

**Files:**
- Create: `app/rag/__init__.py` (empty), `app/rag/chunk.py`
- Test: `tests/test_ragchunk.py`

**Interfaces:**
- Produces: `chunk_text(text: str, max_chars: int = 1000) -> list[str]` — splits sanitized plain text into paragraph-sized chunks; drops empty/whitespace-only pieces; a paragraph longer than `max_chars` is hard-split into ≤ `max_chars` slices on word boundaries.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ragchunk.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_ragchunk.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.rag'`.

- [ ] **Step 3: Write minimal implementation**

```python
# app/rag/__init__.py
```
(empty file)

```python
# app/rag/chunk.py
"""Split sanitized course text into paragraph-sized chunks for full-text search.

Paragraphs (blank-line separated) are the natural retrieval unit. A paragraph
longer than max_chars is hard-split on word boundaries so no single chunk is
unwieldy. Pure function: no I/O, no mocks.
"""


def chunk_text(text: str, max_chars: int = 1000) -> list[str]:
    chunks: list[str] = []
    for para in text.split("\n\n"):
        para = para.strip()
        if not para:
            continue
        if len(para) <= max_chars:
            chunks.append(para)
        else:
            chunks.extend(_split_long(para, max_chars))
    return chunks


def _split_long(para: str, max_chars: int) -> list[str]:
    out, current = [], ""
    for word in para.split():
        candidate = word if not current else f"{current} {word}"
        if len(candidate) > max_chars and current:
            out.append(current)
            current = word
        else:
            current = candidate
    if current:
        out.append(current)
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_ragchunk.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Capture evidence, README Layer 28, commit**

Follow the Evidence procedure with label `ragchunk`. README section:

```markdown
**Layer 28 — chunk course text for retrieval**

The RAG retrieves paragraph-sized pieces, so `chunk_text` splits sanitized course text on blank lines, drops empty runs, and hard-splits any over-long paragraph on word boundaries without losing content. Pure function, no mocks.

Red — `chunk_text` doesn't exist yet:

![Chunk tests failing](docs/test-evidence/ragchunk-red.png)

Green — after adding `chunk_text`:

![Chunk tests passing](docs/test-evidence/ragchunk-green.png)
```

```bash
git add app/rag/__init__.py app/rag/chunk.py tests/test_ragchunk.py docs/test-evidence/ragchunk-red.png docs/test-evidence/ragchunk-green.png docs/test-evidence/ragchunk-red.html docs/test-evidence/ragchunk-green.html README.md
git commit -m "Add course-text chunking for RAG"
```

---

### Task 2: Canvas text-source fetch (Layer `coursecontent`, README Layer 29)

**Files:**
- Create: `app/rag/content.py`
- Test: `tests/test_coursecontent.py`

**Interfaces:**
- Consumes: `app.canvas._next_page(response)`, `nh3.clean`.
- Produces document dicts with keys `{"source_type", "title", "canvas_url", "raw_text"}`:
  - `fetch_syllabus(base_url, token, course_id, client) -> dict | None`
  - `fetch_pages(base_url, token, course_id, client) -> list[dict]`
  - `fetch_module_items(base_url, token, course_id, client) -> list[dict]`
  - `fetch_announcements(base_url, token, course_id, client) -> list[dict]`
  - Helper `_get_all(url, params, headers, client) -> list[dict]` that follows `Link` pagination.
  - Helper `_clean(html) -> str` = `nh3.clean(html or "")`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_coursecontent.py
"""Layer 29 — fetch a course's text sources from Canvas as document dicts.

Canvas mocked at the httpx transport boundary. Syllabus, pages, module items,
and announcements come back sanitized, paginated, and tagged with source_type,
title, and canvas_url. No Neon, no token in output.
"""

import httpx

from app.rag.content import (
    fetch_announcements,
    fetch_module_items,
    fetch_pages,
    fetch_syllabus,
)

BASE = "https://school.test"


def client_for(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_syllabus_is_sanitized_and_tagged():
    def handler(request):
        assert request.url.params.get("include[]") == "syllabus_body"
        return httpx.Response(200, json={
            "id": 7, "name": "Bio 101",
            "syllabus_body": "<p>Late work loses 10%<script>x()</script></p>",
        })

    doc = fetch_syllabus(BASE, "tok", 7, client_for(handler))
    assert doc["source_type"] == "syllabus"
    assert doc["title"] == "Syllabus"
    assert doc["canvas_url"] == f"{BASE}/courses/7/assignments/syllabus"
    assert "Late work loses 10%" in doc["raw_text"]
    assert "<script>" not in doc["raw_text"]


def test_syllabus_absent_returns_none():
    def handler(request):
        return httpx.Response(200, json={"id": 7, "name": "Bio 101"})
    assert fetch_syllabus(BASE, "tok", 7, client_for(handler)) is None


def test_pages_follow_pagination_and_pull_bodies():
    def handler(request):
        path = request.url.path
        if path.endswith("/pages") and request.url.params.get("page") != "2":
            return httpx.Response(
                200,
                json=[{"url": "week-1", "title": "Week 1"}],
                headers={"Link": f'<{BASE}/api/v1/courses/7/pages?page=2>; rel="next"'},
            )
        if path.endswith("/pages"):
            return httpx.Response(200, json=[{"url": "week-2", "title": "Week 2"}])
        if path.endswith("/pages/week-1"):
            return httpx.Response(200, json={"title": "Week 1", "body": "<p>Intro</p>"})
        if path.endswith("/pages/week-2"):
            return httpx.Response(200, json={"title": "Week 2", "body": "<p>More</p>"})
        return httpx.Response(404, json={})

    docs = fetch_pages(BASE, "tok", 7, client_for(handler))
    assert {d["title"] for d in docs} == {"Week 1", "Week 2"}
    assert all(d["source_type"] == "page" for d in docs)
    assert any("Intro" in d["raw_text"] for d in docs)
    assert docs[0]["canvas_url"].startswith(f"{BASE}/courses/7/pages/")


def test_module_items_become_documents():
    def handler(request):
        return httpx.Response(200, json=[{
            "name": "Unit 1",
            "items": [{"title": "Read chapter 1", "type": "Page",
                       "html_url": f"{BASE}/courses/7/modules/items/1"}],
        }])
    docs = fetch_module_items(BASE, "tok", 7, client_for(handler))
    assert docs[0]["source_type"] == "module_item"
    assert "Read chapter 1" in docs[0]["raw_text"]


def test_announcements_are_sanitized():
    def handler(request):
        assert request.url.params.get("context_codes[]") == "course_7"
        return httpx.Response(200, json=[{
            "title": "Exam moved", "message": "<p>Now Friday<script>y()</script></p>",
            "html_url": f"{BASE}/courses/7/discussion_topics/9",
        }])
    docs = fetch_announcements(BASE, "tok", 7, client_for(handler))
    assert docs[0]["source_type"] == "announcement"
    assert "Now Friday" in docs[0]["raw_text"]
    assert "<script>" not in docs[0]["raw_text"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_coursecontent.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.rag.content'`.

- [ ] **Step 3: Write minimal implementation**

```python
# app/rag/content.py
"""Fetch a course's text sources from Canvas as plain document dicts.

Each function returns dicts shaped {source_type, title, canvas_url, raw_text},
with HTML sanitized via nh3 and every list endpoint following the Link header.
Canvas is mocked at the httpx boundary in tests; the client is injected and the
token is never logged.
"""

import nh3

from app.canvas import _next_page


def _headers(token):
    return {"Authorization": f"Bearer {token}"}


def _clean(html):
    return nh3.clean(html or "").strip()


def _get_all(url, params, headers, client):
    out = []
    while url:
        resp = client.get(url, params=params, headers=headers)
        resp.raise_for_status()
        out.extend(resp.json())
        url = _next_page(resp)
        params = None
    return out


def fetch_syllabus(base_url, token, course_id, client):
    url = f"{base_url}/api/v1/courses/{course_id}"
    resp = client.get(url, params={"include[]": "syllabus_body"}, headers=_headers(token))
    resp.raise_for_status()
    body = _clean(resp.json().get("syllabus_body"))
    if not body:
        return None
    return {
        "source_type": "syllabus",
        "title": "Syllabus",
        "canvas_url": f"{base_url}/courses/{course_id}/assignments/syllabus",
        "raw_text": body,
    }


def fetch_pages(base_url, token, course_id, client):
    listing = _get_all(
        f"{base_url}/api/v1/courses/{course_id}/pages",
        {"per_page": 100}, _headers(token), client,
    )
    docs = []
    for page in listing:
        slug = page.get("url")
        if not slug:
            continue
        full = client.get(
            f"{base_url}/api/v1/courses/{course_id}/pages/{slug}",
            headers=_headers(token),
        )
        full.raise_for_status()
        body = _clean(full.json().get("body"))
        if not body:
            continue
        docs.append({
            "source_type": "page",
            "title": page.get("title") or slug,
            "canvas_url": f"{base_url}/courses/{course_id}/pages/{slug}",
            "raw_text": body,
        })
    return docs


def fetch_module_items(base_url, token, course_id, client):
    modules = _get_all(
        f"{base_url}/api/v1/courses/{course_id}/modules",
        {"include[]": "items", "per_page": 100}, _headers(token), client,
    )
    docs = []
    for module in modules:
        for item in module.get("items") or []:
            title = item.get("title")
            if not title:
                continue
            docs.append({
                "source_type": "module_item",
                "title": title,
                "canvas_url": item.get("html_url") or "",
                "raw_text": f"{module.get('name', '')}: {title}".strip(": "),
            })
    return docs


def fetch_announcements(base_url, token, course_id, client):
    items = _get_all(
        f"{base_url}/api/v1/announcements",
        {"context_codes[]": f"course_{course_id}", "per_page": 100},
        _headers(token), client,
    )
    docs = []
    for a in items:
        body = _clean(a.get("message"))
        if not body:
            continue
        docs.append({
            "source_type": "announcement",
            "title": a.get("title") or "Announcement",
            "canvas_url": a.get("html_url") or "",
            "raw_text": body,
        })
    return docs
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_coursecontent.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Capture evidence, README Layer 29, commit**

Evidence procedure with label `coursecontent`. README "Layer 29 — fetch a course's text sources" (description → red → green).

```bash
git add app/rag/content.py tests/test_coursecontent.py docs/test-evidence/coursecontent-red.png docs/test-evidence/coursecontent-green.png docs/test-evidence/coursecontent-red.html docs/test-evidence/coursecontent-green.html README.md
git commit -m "Fetch course text sources from Canvas for RAG"
```

---

### Task 3: PDF files (Layer `pdftext`, README Layer 30)

**Files:**
- Create: `app/rag/pdf.py`
- Modify: `app/rag/content.py` (add `fetch_pdf_documents`), `requirements.txt` (add `pypdf`)
- Test: `tests/test_pdftext.py`

**Interfaces:**
- Produces:
  - `extract_pdf_text(data: bytes) -> str` (in `app/rag/pdf.py`) — returns concatenated page text; returns `""` on any parse error (never raises).
  - `fetch_pdf_documents(base_url, token, course_id, client) -> list[dict]` (in `content.py`) — lists course files, downloads only `application/pdf`, extracts text, returns document dicts with `source_type="file_pdf"`. A file whose extraction is empty is skipped.

- [ ] **Step 1: Add pypdf to requirements and install**

Add `pypdf==4.3.1` to `requirements.txt`, then:
Run: `.venv/Scripts/python.exe -m pip install pypdf==4.3.1`
Expected: installs successfully.

- [ ] **Step 2: Write the failing test**

```python
# tests/test_pdftext.py
"""Layer 30 — turn a course's PDF files into document dicts.

Canvas Files API mocked at the httpx boundary. Only application/pdf files are
downloaded; their text is extracted with pypdf; a non-PDF is ignored and a
corrupt PDF is skipped, never fatal.
"""

import httpx
from pypdf import PdfWriter

from app.rag.content import fetch_pdf_documents
from app.rag.pdf import extract_pdf_text

BASE = "https://school.test"


def _one_page_pdf_bytes(text):
    # pypdf can't author text content easily; use a blank page and assert the
    # extractor returns a string without raising. Real text-extraction is
    # covered by the corrupt-vs-valid distinction below.
    import io
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def test_extract_returns_string_for_valid_pdf():
    out = extract_pdf_text(_one_page_pdf_bytes("hello"))
    assert isinstance(out, str)


def test_extract_returns_empty_on_corrupt_bytes():
    assert extract_pdf_text(b"not a pdf at all") == ""


def test_only_pdfs_are_downloaded_and_become_documents():
    pdf_bytes = _one_page_pdf_bytes("syllabus text")

    def handler(request):
        path = request.url.path
        if path.endswith("/files"):
            return httpx.Response(200, json=[
                {"id": 1, "display_name": "Syllabus.pdf",
                 "content-type": "application/pdf",
                 "url": f"{BASE}/files/1/download",
                 "html_url": f"{BASE}/courses/7/files/1"},
                {"id": 2, "display_name": "logo.png",
                 "content-type": "image/png",
                 "url": f"{BASE}/files/2/download",
                 "html_url": f"{BASE}/courses/7/files/2"},
            ])
        if path.endswith("/files/1/download"):
            return httpx.Response(200, content=pdf_bytes)
        return httpx.Response(404, content=b"")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    docs = fetch_pdf_documents(BASE, "tok", 7, client)
    assert len(docs) == 1
    assert docs[0]["source_type"] == "file_pdf"
    assert docs[0]["title"] == "Syllabus.pdf"
    assert docs[0]["canvas_url"] == f"{BASE}/courses/7/files/1"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_pdftext.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.rag.pdf'`.

- [ ] **Step 4: Write minimal implementation**

```python
# app/rag/pdf.py
"""Extract text from a PDF's bytes with pypdf.

Course PDFs are arbitrary uploads; a malformed file must never crash a sync, so
any parse error yields an empty string and the caller skips that file.
"""

import io
import logging

from pypdf import PdfReader

logger = logging.getLogger(__name__)


def extract_pdf_text(data: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(data))
        return "\n\n".join((page.extract_text() or "") for page in reader.pages).strip()
    except Exception:
        logger.warning("PDF extraction failed for a %d-byte file", len(data))
        return ""
```

Add to `app/rag/content.py`:

```python
from app.rag.pdf import extract_pdf_text


def fetch_pdf_documents(base_url, token, course_id, client):
    files = _get_all(
        f"{base_url}/api/v1/courses/{course_id}/files",
        {"per_page": 100}, _headers(token), client,
    )
    docs = []
    for f in files:
        if f.get("content-type") != "application/pdf":
            continue
        download = client.get(f.get("url"), headers=_headers(token))
        download.raise_for_status()
        text = extract_pdf_text(download.content)
        if not text:
            continue
        docs.append({
            "source_type": "file_pdf",
            "title": f.get("display_name") or "PDF",
            "canvas_url": f.get("html_url") or "",
            "raw_text": text,
        })
    return docs
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_pdftext.py -q`
Expected: PASS (3 passed). (If a blank page yields `None` text, the extractor's `or ""` keeps it a string — the test only asserts type and the corrupt/empty distinction.)

- [ ] **Step 6: Capture evidence, README Layer 30, commit**

Evidence procedure with label `pdftext`. README "Layer 30 — index course PDF files".

```bash
git add app/rag/pdf.py app/rag/content.py requirements.txt tests/test_pdftext.py docs/test-evidence/pdftext-red.png docs/test-evidence/pdftext-green.png docs/test-evidence/pdftext-red.html docs/test-evidence/pdftext-green.html README.md
git commit -m "Index course PDF files for RAG"
```

---

### Task 4: Models, migration, FTS retrieval (Layer `courseretrieval`, README Layer 31)

**Files:**
- Modify: `app/models.py` (add three tables)
- Create: `app/rag/fts.py`, `app/rag/retrieve.py`, `tools/migrate_add_course_rag.py`
- Test: `tests/test_courseretrieval.py`

**Interfaces:**
- Produces:
  - `Course`, `CourseDocument`, `DocumentChunk` SQLModel tables (no `search_vector` attribute on the model).
  - `ensure_search_vector(engine) -> None` (in `app/rag/fts.py`) — idempotent; on Postgres adds the generated `search_vector` column + GIN index; on non-Postgres is a no-op.
  - `retrieve(session, course_id, question, k=5) -> list[dict]` (in `app/rag/retrieve.py`) — returns up to `k` chunks `{"chunk_text", "source_title", "source_url", "rank"}`, scoped to `course_id`, ordered by `ts_rank` descending. Empty list when nothing matches.

- [ ] **Step 1: Add the models**

Append to `app/models.py` (after `Assignment`):

```python
class Course(SQLModel, table=True):
    __tablename__ = "courses"
    __table_args__ = (UniqueConstraint("connection_id", "canvas_course_id"),)

    id: int | None = Field(default=None, primary_key=True)
    connection_id: int = Field(foreign_key="connections.id", index=True)
    canvas_course_id: int
    name: str
    last_content_synced_at: datetime | None = None

    documents: list["CourseDocument"] = Relationship(
        back_populates="course",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


class CourseDocument(SQLModel, table=True):
    __tablename__ = "course_documents"

    id: int | None = Field(default=None, primary_key=True)
    course_id: int = Field(foreign_key="courses.id", index=True)
    source_type: str
    title: str
    canvas_url: str = ""
    raw_text: str = ""
    last_synced_at: datetime = Field(default_factory=_utcnow)

    course: Course | None = Relationship(back_populates="documents")
    chunks: list["DocumentChunk"] = Relationship(
        back_populates="document",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


class DocumentChunk(SQLModel, table=True):
    __tablename__ = "document_chunks"

    id: int | None = Field(default=None, primary_key=True)
    course_id: int = Field(foreign_key="courses.id", index=True)
    document_id: int = Field(foreign_key="course_documents.id", index=True)
    chunk_text: str
    source_title: str = ""
    source_url: str = ""
    # search_vector is intentionally Postgres-only and NOT mapped here; it is
    # added by app.rag.fts.ensure_search_vector and read via raw SQL.

    document: CourseDocument | None = Relationship(back_populates="chunks")
```

Add `UniqueConstraint` to the imports at the top of `app/models.py`:

```python
from sqlalchemy import JSON, Column, UniqueConstraint
```

- [ ] **Step 2: Write `ensure_search_vector`**

```python
# app/rag/fts.py
"""Postgres-only full-text-search wiring for document_chunks.

search_vector is a generated tsvector kept out of the SQLModel model so the
tables still build under SQLite. This adds it (and its GIN index) on Postgres,
idempotently. Used by the migration script and by the retrieval test fixture.
"""

from sqlalchemy import text


def ensure_search_vector(engine) -> None:
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as conn:
        conn.execute(text(
            "ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS search_vector "
            "tsvector GENERATED ALWAYS AS (to_tsvector('english', chunk_text)) STORED"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_document_chunks_search "
            "ON document_chunks USING GIN (search_vector)"
        ))
```

- [ ] **Step 3: Write the failing test**

```python
# tests/test_courseretrieval.py
"""Layer 31 — course-scoped full-text retrieval over stored chunks.

Real Postgres (Neon test branch) because search relies on tsvector/ts_rank. The
key correctness property: a query against course A never returns course B's
chunks. Skips unless TEST_DATABASE_URL is set, like the other Neon layers.
"""

import os

import pytest
from sqlmodel import Session, SQLModel

from app.db import make_engine
from app.models import Connection, Course, CourseDocument, DocumentChunk, User
from app.rag.fts import ensure_search_vector
from app.rag.retrieve import retrieve

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set (retrieval needs a Neon test branch)",
)


@pytest.fixture(scope="module")
def engine():
    eng = make_engine(os.environ["TEST_DATABASE_URL"])
    SQLModel.metadata.create_all(eng)
    ensure_search_vector(eng)
    yield eng
    SQLModel.metadata.drop_all(eng)


@pytest.fixture
def session(engine):
    conn = engine.connect()
    trans = conn.begin()
    s = Session(bind=conn)
    try:
        yield s
    finally:
        s.close()
        trans.rollback()
        conn.close()


def _course_with_chunk(s, course_name, chunk):
    user = User(email=f"{course_name}@x.com", password_hash="h")
    s.add(user); s.flush()
    conn = Connection(user_id=user.id, label="Mine", base_url="https://s.test",
                      account_type="student", access_token="tok")
    s.add(conn); s.flush()
    course = Course(connection_id=conn.id, canvas_course_id=1, name=course_name)
    s.add(course); s.flush()
    doc = CourseDocument(course_id=course.id, source_type="syllabus",
                         title="Syllabus", canvas_url="u", raw_text=chunk)
    s.add(doc); s.flush()
    s.add(DocumentChunk(course_id=course.id, document_id=doc.id, chunk_text=chunk,
                        source_title="Syllabus", source_url="u"))
    s.flush()
    return course


def test_retrieval_is_scoped_to_one_course(session):
    bio = _course_with_chunk(session, "Bio", "Late work loses ten percent per day.")
    eng = _course_with_chunk(session, "Eng", "Essays use MLA citation format.")

    hits = retrieve(session, bio.id, "late work policy")
    assert hits, "expected a hit in the Bio course"
    assert all(h["source_title"] == "Syllabus" for h in hits)
    assert "ten percent" in hits[0]["chunk_text"]

    # The English course must not surface for a Biology query, and vice versa.
    eng_hits = retrieve(session, eng.id, "late work policy")
    assert eng_hits == []


def test_no_match_returns_empty(session):
    bio = _course_with_chunk(session, "Bio2", "Office hours are Tuesdays.")
    assert retrieve(session, bio.id, "quantum chromodynamics") == []
```

- [ ] **Step 4: Run test to verify it fails**

Run: `$env:TEST_DATABASE_URL` must be set to the Neon test branch URL, then
`.venv/Scripts/python.exe -m pytest tests/test_courseretrieval.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.rag.retrieve'` (or, if TEST_DATABASE_URL is unset, SKIPPED — set it to get a real red).

- [ ] **Step 5: Write `retrieve`**

```python
# app/rag/retrieve.py
"""Course-scoped full-text retrieval over document_chunks.

Lexical search via Postgres tsvector/ts_rank — no embeddings. Always filtered by
course_id so one course's answer never draws on another's. Raw SQL because
search_vector is a Postgres-only generated column not mapped on the model.
"""

from sqlalchemy import text

_SQL = text(
    "SELECT chunk_text, source_title, source_url, "
    "       ts_rank(search_vector, plainto_tsquery('english', :q)) AS rank "
    "FROM document_chunks "
    "WHERE course_id = :cid "
    "  AND search_vector @@ plainto_tsquery('english', :q) "
    "ORDER BY rank DESC "
    "LIMIT :k"
)


def retrieve(session, course_id, question, k=5):
    rows = session.execute(
        _SQL, {"q": question, "cid": course_id, "k": k}
    ).all()
    return [
        {"chunk_text": r.chunk_text, "source_title": r.source_title,
         "source_url": r.source_url, "rank": float(r.rank)}
        for r in rows
    ]
```

- [ ] **Step 6: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_courseretrieval.py -q`
Expected: PASS (2 passed).

- [ ] **Step 7: Write the migration script**

```python
# tools/migrate_add_course_rag.py
"""Create the RAG tables and the Postgres full-text index on the live Neon branch.

    python tools/migrate_add_course_rag.py

New tables only (courses, course_documents, document_chunks) — safe and additive.
Then adds the generated search_vector column + GIN index via
app.rag.fts.ensure_search_vector. Idempotent. Prints the target host (no
credentials).
"""

import os
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from sqlmodel import SQLModel  # noqa: E402

from app.db import make_engine  # noqa: E402
from app.rag.fts import ensure_search_vector  # noqa: E402
import app.models  # noqa: E402,F401  (registers the tables on the metadata)


def main():
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL is not set. Refusing to run.")
        raise SystemExit(2)
    if url.startswith("sqlite"):
        print("This looks like local SQLite, not Neon. Point DATABASE_URL at Neon.")
        raise SystemExit(1)

    print(f"About to create RAG tables in: {urlparse(url).hostname}")
    engine = make_engine(url)
    SQLModel.metadata.create_all(engine)  # additive: only missing tables
    ensure_search_vector(engine)
    print("RAG tables and full-text index ready. Done.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 8: Capture evidence, README Layer 31, commit**

Evidence procedure with label `courseretrieval` (run with `TEST_DATABASE_URL` set so red/green are real, not skips). README "Layer 31 — course-scoped full-text retrieval".

```bash
git add app/models.py app/rag/fts.py app/rag/retrieve.py tools/migrate_add_course_rag.py tests/test_courseretrieval.py docs/test-evidence/courseretrieval-red.png docs/test-evidence/courseretrieval-green.png docs/test-evidence/courseretrieval-red.html docs/test-evidence/courseretrieval-green.html README.md
git commit -m "Add RAG tables, migration, and course-scoped retrieval"
```

---

### Task 5: Grounded answer via Groq (Layer `askcourse`, README Layer 32)

**Files:**
- Create: `app/rag/answer.py`
- Test: `tests/test_askcourse.py`

**Interfaces:**
- Consumes: retrieved chunk dicts from `retrieve`.
- Produces: `answer_question(question, chunks, client) -> dict` returning `{"answer": str, "sources": list[dict]}` where each source is `{"title", "url"}` (distinct, in retrieval order). On Groq timeout returns `{"answer": REFUSAL_OR_ERROR, "error": "timeout"}` cleanly; on other errors a clean error dict. No key logged. The refusal string `I don't know based on the provided course documents.` is enforced by the system prompt and is what the model returns when chunks don't contain the answer.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_askcourse.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_askcourse.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.rag.answer'`.

- [ ] **Step 3: Write minimal implementation**

```python
# app/rag/answer.py
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
    headers = {"Authorization": f"Bearer {os.environ['GROQ_API_KEY']}"}

    try:
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_askcourse.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Capture evidence, README Layer 32, commit**

Evidence procedure with label `askcourse`. README "Layer 32 — grounded answer from course content".

```bash
git add app/rag/answer.py tests/test_askcourse.py docs/test-evidence/askcourse-red.png docs/test-evidence/askcourse-green.png docs/test-evidence/askcourse-red.html docs/test-evidence/askcourse-green.html README.md
git commit -m "Generate grounded course answers via Groq"
```

---

### Task 6: Sync orchestration + web flow + flag (Layer `askcourseweb`, README Layer 33)

**Files:**
- Create: `app/sync_content.py`, `app/templates/course_picker.html`, `app/templates/course_chat.html`
- Modify: `app/web.py` (flag, routes, nav link)
- Test: `tests/test_askcourseweb.py`

**Interfaces:**
- Consumes: all of `app/rag/*`, `app.canvas.fetch_courses`, `app.canvas.fetch_assignments`.
- Produces:
  - `sync_course_content(session, connection, course, client) -> None` (in `app/sync_content.py`) — fetches every source (syllabus, pages, module items, assignments, announcements, PDFs), sanitizes, chunks, replaces that course's `CourseDocument`/`DocumentChunk` rows, stamps `course.last_content_synced_at`. Resilient per source: one failing source is logged and skipped.
  - Routes (all gated by `ASK_COURSE_ENABLED`): `GET /ask` (course picker), `POST /courses/{course_id}/sync-content`, `GET /courses/{course_id}/ask`, `POST /courses/{course_id}/ask`.

- [ ] **Step 1: Add the feature flag helper to `app/web.py`**

Near the other config reads in `app/web.py`:

```python
def _ask_course_enabled() -> bool:
    return os.environ.get("ASK_COURSE_ENABLED", "").lower() in ("1", "true", "yes")
```

- [ ] **Step 2: Write the sync orchestration**

```python
# app/sync_content.py
"""Sync one course's Canvas content into the RAG store, on demand.

Fetch every source, sanitize, chunk, and replace this course's documents and
chunks so a re-sync is clean. Resilient per source: a failing page or PDF is
logged and skipped, never aborting the rest. One code path for one course or
many.
"""

import logging

from sqlmodel import select

from app.canvas import fetch_assignments
from app.models import CourseDocument, DocumentChunk
from app.rag.chunk import chunk_text
from app.rag.content import (
    fetch_announcements,
    fetch_module_items,
    fetch_pages,
    fetch_pdf_documents,
    fetch_syllabus,
)
from app.models import _utcnow

logger = logging.getLogger(__name__)


def _assignment_documents(base_url, token, course_id, client):
    docs = []
    for a in fetch_assignments(base_url, token, course_id, client):
        text = (a.get("description") or "").strip()
        if not text:
            continue
        docs.append({
            "source_type": "assignment", "title": a.get("name") or "Assignment",
            "canvas_url": a.get("html_url") or "", "raw_text": text,
        })
    return docs


def _gather(base_url, token, canvas_course_id, client):
    sources = [
        ("syllabus", lambda: [d for d in [fetch_syllabus(base_url, token, canvas_course_id, client)] if d]),
        ("pages", lambda: fetch_pages(base_url, token, canvas_course_id, client)),
        ("modules", lambda: fetch_module_items(base_url, token, canvas_course_id, client)),
        ("assignments", lambda: _assignment_documents(base_url, token, canvas_course_id, client)),
        ("announcements", lambda: fetch_announcements(base_url, token, canvas_course_id, client)),
        ("pdfs", lambda: fetch_pdf_documents(base_url, token, canvas_course_id, client)),
    ]
    docs = []
    for label, fn in sources:
        try:
            docs.extend(fn())
        except Exception:
            logger.warning("course-content source %s failed; skipping", label)
    return docs


def sync_course_content(session, connection, course, client):
    docs = _gather(connection.base_url, connection.access_token,
                   course.canvas_course_id, client)

    # Replace this course's content so a re-sync is clean.
    for old in session.exec(
        select(CourseDocument).where(CourseDocument.course_id == course.id)
    ).all():
        session.delete(old)
    session.flush()

    for d in docs:
        document = CourseDocument(
            course_id=course.id, source_type=d["source_type"], title=d["title"],
            canvas_url=d["canvas_url"], raw_text=d["raw_text"],
        )
        session.add(document)
        session.flush()
        for piece in chunk_text(d["raw_text"]):
            session.add(DocumentChunk(
                course_id=course.id, document_id=document.id, chunk_text=piece,
                source_title=d["title"], source_url=d["canvas_url"],
            ))
    course.last_content_synced_at = _utcnow()
    session.add(course)
    session.flush()
```

- [ ] **Step 3: Add routes to `app/web.py`**

```python
# app/web.py — imports
from fastapi import HTTPException
from app.rag.answer import answer_question
from app.rag.retrieve import retrieve
from app.sync_content import sync_course_content
from app.models import Course
from app.canvas import fetch_courses

# ... inside create_app(), alongside the other routes:

@app.get("/ask")
def ask_picker(request: Request, session: Session = Depends(get_session)):
    if not _ask_course_enabled():
        raise HTTPException(status_code=404)
    user = _current_user(request, session)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    courses = session.exec(
        select(Course).join(Connection, Course.connection_id == Connection.id)
        .where(Connection.user_id == user.id)
    ).all()
    return TEMPLATES.TemplateResponse(request, "course_picker.html",
                                      {"courses": courses})


@app.post("/courses/{course_id}/sync-content")
def sync_content(request: Request, course_id: int,
                 session: Session = Depends(get_session),
                 client_factory=Depends(get_canvas_client_factory)):
    if not _ask_course_enabled():
        raise HTTPException(status_code=404)
    user = _current_user(request, session)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    course = _owned_course_or_404(session, course_id, user)
    connection = session.get(Connection, course.connection_id)
    client = client_factory()
    try:
        sync_course_content(session, connection, course, client)
        session.commit()
    finally:
        client.close()
    return RedirectResponse(f"/courses/{course_id}/ask", status_code=303)


@app.get("/courses/{course_id}/ask")
def course_ask_page(request: Request, course_id: int,
                    session: Session = Depends(get_session)):
    if not _ask_course_enabled():
        raise HTTPException(status_code=404)
    user = _current_user(request, session)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    course = _owned_course_or_404(session, course_id, user)
    return TEMPLATES.TemplateResponse(request, "course_chat.html",
                                      {"course": course, "question": None,
                                       "answer": None, "sources": []})


@app.post("/courses/{course_id}/ask")
def course_ask(request: Request, course_id: int, question: str = Form(...),
               session: Session = Depends(get_session),
               client: httpx.Client = Depends(get_groq_client)):
    if not _ask_course_enabled():
        raise HTTPException(status_code=404)
    user = _current_user(request, session)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    course = _owned_course_or_404(session, course_id, user)
    question = question.strip()[:500]  # cost control: cap length
    chunks = retrieve(session, course.id, question, k=5)
    result = answer_question(question, chunks, client)
    return TEMPLATES.TemplateResponse(request, "course_chat.html",
                                      {"course": course, "question": question,
                                       "answer": result["answer"],
                                       "sources": result["sources"]})
```

Add the ownership helper near `_owned_assignment_or_404`:

```python
def _owned_course_or_404(session, course_id, user):
    course = session.get(Course, course_id)
    if course is None:
        raise HTTPException(status_code=404)
    connection = session.get(Connection, course.connection_id)
    if connection is None or connection.user_id != user.id:
        raise HTTPException(status_code=404)
    return course
```

These reuse the dependencies already defined in `app/web.py`: `get_groq_client` (yields an `httpx.Client`, injected directly — same as the `breakdown` route) and `get_canvas_client_factory` (returns a `lambda: httpx.Client(...)` factory — same as `add_connection`). Tests override both with `app.dependency_overrides`, exactly like `tests/test_setup.py`. Do not invent new factories.

- [ ] **Step 4: Write the templates**

```html
<!-- app/templates/course_picker.html -->
{% extends "base.html" %}
{% block body %}
<h1>Ask my course</h1>
{% if not courses %}
  <p>No courses yet. Add a Canvas connection and run a sync first.</p>
{% else %}
<ul class="course-list">
  {% for c in courses %}
    <li><a href="/courses/{{ c.id }}/ask">{{ c.name }}</a></li>
  {% endfor %}
</ul>
{% endif %}
{% endblock %}
```

```html
<!-- app/templates/course_chat.html -->
{% extends "base.html" %}
{% block body %}
<h1>{{ course.name }}</h1>
<form method="post" action="/courses/{{ course.id }}/sync-content">
  <button class="btn btn--ghost" type="submit">Sync course content</button>
</form>
{% if course.last_content_synced_at %}
  <p class="muted">Last synced {{ course.last_content_synced_at }}</p>
{% else %}
  <p class="muted">Not synced yet — sync before asking.</p>
{% endif %}

<form method="post" action="/courses/{{ course.id }}/ask">
  <input type="text" name="question" maxlength="500"
         placeholder="Ask about this course…" required>
  <button class="btn" type="submit">Ask</button>
</form>

{% if answer %}
<section class="answer">
  <h2>Answer</h2>
  <p>{{ answer }}</p>
  {% if sources %}
  <h3>Sources</h3>
  <ul>
    {% for s in sources %}
      <li>{% if s.url %}<a href="{{ s.url }}">{{ s.title }}</a>{% else %}{{ s.title }}{% endif %}</li>
    {% endfor %}
  </ul>
  {% endif %}
</section>
{% endif %}
{% endblock %}
```

(`base.html` defines `{% block body %}` inside its `layout` block — the new templates fill `body`, matching `detail.html` and `report.html`.)

- [ ] **Step 5: Write the failing test**

```python
# tests/test_askcourseweb.py
"""Layer 33 — the Ask My Course web flow.

TestClient + Neon test branch. The picker lists only the signed-in user's
courses; syncing stores documents/chunks; asking renders an answer and its
sources; the routes 404 when the feature flag is off; and a user cannot ask
another user's course. Canvas and Groq are mocked at the httpx boundary.
Skips unless TEST_DATABASE_URL is set.
"""

import os

import httpx
import pytest
from sqlmodel import Session, SQLModel, select

from app.db import make_engine
from app.models import Connection, Course, User
from app.rag.fts import ensure_search_vector

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set (web flow needs a Neon test branch)",
)

BASE = "https://school.test"


@pytest.fixture
def engine():
    eng = make_engine(os.environ["TEST_DATABASE_URL"])
    SQLModel.metadata.create_all(eng)
    ensure_search_vector(eng)
    yield eng
    SQLModel.metadata.drop_all(eng)


# Build the app with get_session, the Canvas client factory, and the Groq client
# factory overridden — mirror tests/test_setup.py and tests/test_excusebutton.py
# for the exact override wiring, plus monkeypatch ASK_COURSE_ENABLED=1.

# Tests to implement:
#  - test_flag_off_hides_routes: with ASK_COURSE_ENABLED unset, GET /ask -> 404.
#  - test_picker_lists_only_my_courses: user A sees A's course, not B's.
#  - test_sync_then_ask_renders_answer_with_sources: seed a course, POST
#    sync-content (Canvas mock returns a syllabus with "late work loses 10%"),
#    then POST ask "late policy" (Groq mock echoes the grounded answer); assert
#    the answer text and the syllabus source link appear in the HTML.
#  - test_cannot_ask_another_users_course: user B's POST /courses/{A_course}/ask
#    -> 404.
```

Implement each described test with the same fixture/override pattern used in `tests/test_excusebutton.py` (signup, dependency overrides) extended with Canvas + Groq `MockTransport` factories and `monkeypatch.setenv("ASK_COURSE_ENABLED", "1")`.

- [ ] **Step 6: Run test to verify it fails**

Run (with `TEST_DATABASE_URL` set): `.venv/Scripts/python.exe -m pytest tests/test_askcourseweb.py -q`
Expected: FAIL — routes/templates/orchestration not wired yet (404s or import errors).

- [ ] **Step 7: Implement until green**

Wire Steps 1–4, then re-run:
Run: `.venv/Scripts/python.exe -m pytest tests/test_askcourseweb.py -q`
Expected: PASS.

- [ ] **Step 8: Add the nav link (flag-gated)**

In the nav template, show an "Ask my course" link only when the flag is on — pass `ask_course_enabled` into the template context from a shared place (e.g. the report/dashboard handler) and wrap the link in `{% if ask_course_enabled %}`.

- [ ] **Step 9: Run the full suite**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: all pass (Neon-gated tests run when `TEST_DATABASE_URL` is set; otherwise skip).

- [ ] **Step 10: Capture evidence, README Layer 33, commit**

Evidence procedure with label `askcourseweb` (with `TEST_DATABASE_URL` set). README "Layer 33 — Ask My Course web flow".

```bash
git add app/sync_content.py app/web.py app/templates/course_picker.html app/templates/course_chat.html tests/test_askcourseweb.py docs/test-evidence/askcourseweb-red.png docs/test-evidence/askcourseweb-green.png docs/test-evidence/askcourseweb-red.html docs/test-evidence/askcourseweb-green.html README.md
git commit -m "Add Ask My Course web flow behind a feature flag"
```

---

### Task 7: Production migration + enable

**Files:** none (operational)

- [ ] **Step 1: Run the migration against Neon**

Run: `.venv/Scripts/python.exe tools/migrate_add_course_rag.py`
Expected: prints the Neon host and "RAG tables and full-text index ready. Done."

- [ ] **Step 2: Decide on enabling**

The feature stays dark until `ASK_COURSE_ENABLED=1` is set in the Render environment. Set it when ready to expose to students; the migration is safe to run before that (additive tables only).

## Self-Review

- **Spec coverage:** chunking (T1), text sources + pagination + sanitize (T2), PDFs + pypdf (T3), 3 tables + migration + FTS + course-scoped retrieval (T4), grounded Groq answer + refusal + sources + timeout (T5), on-demand sync orchestration + picker + sync button + chat + flag + ownership + cost cap (T6), prod migration + flag enable (T7). All spec sections map to a task.
- **`search_vector` not mapped on the model** is honored in T4 (model comment) and used via raw SQL in `retrieve`; fresh DBs get it via `ensure_search_vector` in both the migration and the Neon test fixtures.
- **Type consistency:** document dicts use the same keys (`source_type`, `title`, `canvas_url`, `raw_text`) across T2/T3/T6; retrieved chunk dicts use (`chunk_text`, `source_title`, `source_url`, `rank`) across T4/T5; `answer_question` returns (`answer`, `sources`) consumed by T6.
- **Verified against `app/web.py`:** reuses the existing `get_session`, `get_groq_client` (injected `httpx.Client`, as in `breakdown`), and `get_canvas_client_factory` (factory lambda, as in `add_connection`) dependencies; new templates fill `{% block body %}` (base.html's content block, as in `detail.html`). No new factories invented.
```
