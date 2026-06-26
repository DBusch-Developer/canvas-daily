# Canvas Daily: Ask My Course (course-scoped RAG)

Date: 2026-06-26

## Problem

Students get a daily report and an on-demand AI study breakdown per assignment,
but they can't ask free-form questions about a course — "what's the late-work
policy?", "when is the midterm?", "what does the syllabus say about citations?"
The answers live scattered across the syllabus, pages, modules, assignment
descriptions, announcements, and uploaded PDFs. We want a per-course question
box that answers **only** from that course's own materials and shows its sources.

This is a retrieval-augmented generation (RAG) feature: retrieve the relevant
chunks of a course's content, then have the LLM write a grounded answer from
only those chunks.

## Decisions

- **Groq-only. No embeddings, no second AI vendor, no cost.** A RAG needs an LLM
  only for the *generation* half. Groq already does that. The *retrieval* half
  needs no LLM and no embeddings: we use **Postgres full-text search** (lexical
  retrieval, the same class of technique as TF-IDF), which is native to the Neon
  database the content already lives in. pgvector / OpenAI embeddings are
  explicitly rejected — they would add a paid vendor for no necessary gain.
- **Knowledge base = synced Canvas course content**, not user uploads. The course
  is the unit of scope.
- **Retrieval is scoped by course.** Every retrieval query filters
  `WHERE course_id = ?` so one course's answers never draw on another's. This is
  the single most important correctness property of the feature.
- **A small `courses` table** anchors the picker and the scope key, keyed by
  `(connection_id, canvas_course_id)`. Built from the existing `fetch_courses`.
- **v1 indexes text sources *and* PDFs.** Text sources (syllabus, pages,
  module items, assignment descriptions, announcements) come back as HTML/text
  from the Canvas API and reuse the existing `nh3` sanitize step. PDFs add the
  Files API download plus **pypdf** (pure-Python, no native build) for text
  extraction.
- **Content sync is on-demand per course**, via a "Sync course content" button —
  not folded into the daily assignment sync. Course content changes rarely and
  PDF handling is heavy; a daily refresh can be a later layer.
- **Detail/answer reads from storage, never live Canvas.** Consistent with the
  rest of the app: a question never triggers a live Canvas call.
- **Grounded or it refuses.** If the answer isn't in the retrieved context, the
  model replies exactly: `I don't know based on the provided course documents.`
- **Behind a feature flag** (`ASK_COURSE_ENABLED`). Routes and nav stay hidden on
  prod until we flip it on, because this ships to real students.
- **Built as its own TDD-evidence layers**, one `tests/test_<label>.py` per
  layer, each with a live red and a green screenshot and its own numbered README
  section — never folded into an existing layer.

## Architecture & data flow

```txt
Sync course content (on-demand, per course)
  Canvas APIs → text sources (syllabus, pages, modules/items, assignments,
                              announcements)
              → files → download PDFs → pypdf extract text
  → sanitize HTML (nh3)
  → chunk into paragraph-sized pieces
  → upsert course_documents + document_chunks (course-scoped, tsvector)

Ask (per-course chat page, server-rendered Jinja, no HTMX)
  question → FTS retrieve top-k chunks WHERE course_id = selected, ranked
          → assemble context (chunk_text + source title/url)
          → Groq, grounded system prompt (answer only from context; else the
            exact refusal line)
          → render answer + Sources list (distinct documents: title + Canvas link)
```

## Data model (3 new tables, `app/models.py`)

- **`courses`**
  - `id`, `connection_id` (FK → connections.id, indexed),
    `canvas_course_id` (int), `name` (str), `last_content_synced_at`
    (datetime | None)
  - unique constraint on `(connection_id, canvas_course_id)`
  - relationship: a `Connection` has many `Course`; cascade delete to documents
- **`course_documents`**
  - `id`, `course_id` (FK → courses.id, indexed), `source_type` (str:
    `syllabus` | `page` | `module_item` | `assignment` | `announcement` |
    `file_pdf`), `title` (str), `canvas_url` (str), `raw_text` (str, sanitized),
    `last_synced_at` (datetime)
- **`document_chunks`**
  - `id`, `course_id` (FK, indexed — denormalized so the scoped query is one
    filter), `document_id` (FK → course_documents.id), `chunk_text` (str),
    `source_title` (str), `source_url` (str)
  - `search_vector`: a Postgres **generated** column
    `tsvector GENERATED ALWAYS AS (to_tsvector('english', chunk_text)) STORED`
    with a **GIN index**. **Deliberately *not* a SQLModel-mapped column** — it is
    created Postgres-side by the migration (and a Postgres-only DDL event for
    fresh `create_all`), and read via raw SQL in `retrieve`. Keeping it out of the
    model is what lets these tables still build under SQLite, so the existing
    `create_all`-on-SQLite suite is unaffected; only the Neon-backed retrieval
    layer touches FTS.

Migration is **prod-safe**: these are *new tables*, so creation does not touch
existing data (unlike adding a column). A dedicated idempotent migration script
(`tools/migrate_add_course_rag.py`) creates the tables, the generated column, and
the GIN index, mirroring the existing `tools/migrate_*` scripts. Fresh databases
get them via `tools/init_db.py` / `create_all`.

## New units

- **`app/rag/chunk.py` — `chunk_text(text) -> list[str]`** (pure). Splits
  sanitized text into paragraph-sized chunks with a sane max length, dropping
  empty/whitespace-only pieces. No mocks, no I/O.
- **`app/rag/content.py` — Canvas content fetch** (Canvas mocked at the httpx
  boundary, client injected, token never logged). One function per source type
  returning `{source_type, title, canvas_url, raw_text}` dicts, all following
  `Link`-header pagination and sanitizing HTML before returning:
  - syllabus (`GET /courses/:id?include[]=syllabus_body`)
  - pages (`GET /courses/:id/pages` then page bodies)
  - module items (`GET /courses/:id/modules?include[]=items`)
  - assignments (reuse existing fetch; map descriptions)
  - announcements (`GET /announcements?context_codes[]=course_:id`)
- **`app/rag/pdf.py` — `extract_pdf_text(data: bytes) -> str`** via pypdf, plus a
  Files API lister/downloader in `content.py` (only `application/pdf` files).
  Extraction failures are caught and skipped, never fatal to the sync.
- **`app/rag/retrieve.py` — `retrieve(session, course_id, question, k=5)`**.
  FTS query: `to_tsquery`/`plainto_tsquery` over `search_vector`,
  `WHERE course_id = :course_id`, `ORDER BY ts_rank(...) DESC LIMIT k`. Returns
  the top chunks with their source title/url and rank score.
- **`app/rag/answer.py` — grounded generation via Groq** (Groq mocked in tests).
  Assembles the retrieved chunks into a context block, calls Groq with a strict
  grounded system prompt at low temperature (~0.1), and returns
  `{answer, sources}` where `sources` is the distinct documents behind the
  retrieved chunks. Reuses `app/ai.py`'s error handling: timeout → clean
  "took too long," any other error → clear message. The Groq key rides in the
  Authorization header and is never logged.
- **`app/sync_content.py` — `sync_course_content(session, connection, course, client)`**.
  Orchestrates fetch → sanitize → chunk → upsert, **resilient per source** (a
  failing page or PDF marks nothing fatal and does not abort the rest), then
  stamps `course.last_content_synced_at`. One code path for one course or many.
- **`app/web.py` routes** (all gated by `ASK_COURSE_ENABLED`):
  - course picker page (lists the user's courses from `fetch_courses` / `courses`
    rows; ownership-checked)
  - `POST /courses/{course_id}/sync-content` → runs the content sync, redirects
    back with a freshness stamp
  - course chat page: `GET` renders the question box + last answer; `POST` takes a
    question, retrieves, answers, renders answer + Sources. Server-rendered form,
    no HTMX (consistent with the Mark-excused pattern).

## Grounded-answer contract

- Retrieve top-k (k=5 to start) course-scoped chunks.
- System prompt: answer **only** using the provided course documents; if the
  answer is not present, reply exactly
  `I don't know based on the provided course documents.`; invent no citations,
  titles, or URLs (reuses the existing "never invent citations" rule).
- Temperature ~0.1 (grounded factual QA, not the 0.5 brainstorming breakdown).
- Sources shown under the answer = distinct documents behind the retrieved
  chunks, each as title + Canvas link.
- Cost controls (ships to students): cap question length, cap k and per-chunk
  size in the assembled context, and a simple per-user daily question limit.

## Test plan (TDD — live red before each implementation)

Each is its own layer with its own `tests/test_<label>.py`, live red/green
screenshots, and a numbered README section.

1. **`ragchunk`** — `chunk_text` splits paragraphs, respects max length, drops
   empties. Pure, no mocks. (SQLite-free.)
2. **`coursecontent`** — Canvas content fetch mocked at the httpx boundary:
   syllabus, pages, module items, assignments, announcements; `Link` pagination
   followed; HTML sanitized; correct `source_type`/`title`/`canvas_url`.
3. **`pdftext`** — `extract_pdf_text` returns text from sample PDF bytes; a
   corrupt/empty PDF is skipped cleanly; the Files lister selects only PDFs.
4. **`courseretrieval`** — FTS top-k, course-scoped, ranked, on the **Neon test
   branch** (needs `TEST_DATABASE_URL`; guarded skip like `test_sync.py`).
   Asserts a query in course A never returns course B's chunks.
5. **`askcourse`** — grounded answer with **Groq mocked**: context assembled from
   retrieved chunks; the exact refusal string when the answer isn't in context;
   sources returned as distinct documents; timeout → clean error; no key logged.
6. **`askcourseweb`** — web flow via `TestClient`: picker lists only the user's
   courses; sync-content button stores documents/chunks; asking renders an answer
   and Sources; flag-gated routes are hidden when `ASK_COURSE_ENABLED` is off;
   ownership enforced (no asking another user's course).

DB-backed layers (4, 6) skip unless `TEST_DATABASE_URL` is set, matching the
existing sync/setup layers. Pure and mocked layers (1, 2, 3, 5) run everywhere,
including the pre-commit hook and CI.

## Test evidence

Six new layers, each with `<label>-red.png` / `<label>-green.png` in
`docs/test-evidence/` and a numbered README "Layer N — …" section
(description → red → green), red captured live via
`tools/run_to_html.py <label>-red <pytest target>` before the implementation
exists. `tools/check_evidence.py`, the pre-commit hook, and CI enforce presence.

## Dependencies & config

- Add **pypdf** to `requirements.txt` (pure-Python).
- New env: `ASK_COURSE_ENABLED` (flag). Groq key unchanged (`GROQ_API_KEY`).
- Migration script `tools/migrate_add_course_rag.py`, run against Neon before the
  flag is flipped on.

## Out of scope (v1)

- **Daily auto-refresh of course content** — on-demand sync only; daily refresh
  is a later layer.
- **Embeddings / semantic search** — lexical FTS only; revisit only if retrieval
  quality proves insufficient.
- **User-uploaded knowledge bases** — knowledge base is Canvas course content.
- **Non-PDF files** (docx, pptx, images/OCR) — only `application/pdf` in v1.
- **Cross-course or whole-account questions** — strictly one selected course.
- **Discussion threads** beyond the announcement-style prompts — optional, later.
```
