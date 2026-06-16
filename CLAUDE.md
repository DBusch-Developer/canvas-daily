# CLAUDE.md — Canvas Daily

Context and rules for working on this repo. Read before editing.

## What this is
Canvas Daily is an early-warning layer on top of Canvas LMS. One user owns many Canvas connections; a daily job fetches and stores full assignment detail; a grouped daily email links to instant app-rendered detail pages. Each detail page can generate an AI study breakdown on demand. It does NOT replace Canvas — work is still done and submitted in Canvas.

Two APIs: **Canvas** (third-party assignment data) and **Groq / Llama 3.3 70B** (AI study breakdown).

## Stack
- FastAPI — setup screens, report, detail pages, AI endpoint
- SQLModel — ORM over Neon (Postgres)
- Jinja2 — server-rendered templates
- Groq via OpenAI-compatible API — `base_url = https://api.groq.com/openai/v1`, model `llama-3.3-70b-versatile`
- pytest — test runner
- cron — daily job · SMTP — email delivery
- One language, one test runner, one deploy.

## Hard rules
- **TDD-first. Always.** Red, green, refactor. No production code without a failing test first. Layer inside-out: pure functions → fetch (mocked) → ORM integration → E2E.
- **Every TDD layer is documented with a captured red and green terminal screenshot, committed and linked in the README.** The red is captured *live, before the implementation exists* — never a reenactment once code is written. No layer is "done" without both images in `docs/test-evidence/` and referenced in the README. Full procedure below under **Test evidence**.
- **Tokens are encrypted at rest. Never store, print, or log a token in plaintext.** Not in commits, not in logs, not in error messages.
- **Base URL lives on the connection, never as global config.** Institutions are on different Canvas domains.
- **Sanitize Canvas `description` HTML before it touches a page or email.** Never pipe raw Canvas HTML straight through.
- **Guard null scores.** Score is null until graded — render ungraded work cleanly, never blank or zero.
- **Follow `Link` header pagination.** Never take only page one of a Canvas fetch.
- **One code path for one connection and for four.** Don't special-case single-connection users.
- **Detail pages read from storage, not live Canvas.** No live Canvas call on click.
- **AI breakdown is on-demand, not pre-fetched.** It fires when the user presses the button, not on the daily run.
- **Groq key comes from `GROQ_API_KEY` in the environment.** Never hardcoded, never logged, never in a commit.
- **The AI breakdown keeps its four-section format** — What's being asked / Step-by-step plan / Watch out for / Time estimate — capped under 300 words, temperature 0.5.
- **Handle AI failures cleanly.** Timeout → clear "took too long" message; any other error → clear message. Never a broken page.
- **Never commit `.env`, tokens, or secrets.**

## How I want you to work with me
- Give me **complete files**, not patch snippets or inline diffs. I paste the whole thing in.
- **Short git commit messages.** One line unless I ask for more.
- **Plain language over jargon.** Explain it like you'd say it out loud.
- When you simplify or explain, **strip specific variable names and code references** unless I ask for them.
- Push back immediately if I'm wrong. Acknowledge and correct — don't hedge.
- Bulleted lists use real bullets.

## Test layers (what "done" means)
- **Pure functions** — date/bucketing helpers. Known in, known out, no mocks.
- **Canvas fetch** — Canvas mocked. Pagination, include[]=submission parsing, sanitization, null score.
- **AI breakdown** — Groq mocked. Context assembly, four-section output, timeout → 504, no key in logs.
- **Integration** — real Neon test branch. One-to-many model, round-trip, report query.
- **E2E** — sign up, add connection, view report, click into detail, generate breakdown.

## Test evidence — mandatory, every layer
Each TDD layer gets two committed screenshots: the failing **red** (captured before any implementation), then the passing **green**. Both live in `docs/test-evidence/` and are linked from the README's "Test evidence" section. This is not optional and not a one-time thing — it happens for every layer, forever.

The harness is `tools/run_to_html.py`. It runs pytest with color forced on and renders the output to a terminal-styled HTML page in `docs/test-evidence/<label>.html`. A headless browser then screenshots that page into a PNG.

Procedure for a layer (example label `canvas`):

1. **Write the failing test first.** Implementation does not exist yet.
2. **Capture red — live, before writing code:** `python tools/run_to_html.py canvas-red tests/test_canvas.py` (run via the venv: `.venv\Scripts\python.exe`). Confirm it reports RED.
3. **Screenshot the red page.** Serve the folder over local HTTP — `file://` is blocked in the browser tool: `python -m http.server 8731 --directory docs/test-evidence`. Navigate the browser to `http://127.0.0.1:8731/canvas-red.html` and screenshot the `.frame` element to `canvas-red.png`. Move the PNG into `docs/test-evidence/`.
4. **Write the implementation** until the test passes.
5. **Capture green:** `python tools/run_to_html.py canvas-green tests/test_canvas.py` → confirm GREEN → screenshot `canvas-green.html` the same way → `canvas-green.png`.
6. **Verify the images by eye** — every line legible, no black-on-black, red shows red FAILED, green shows green passed.
7. **Add both PNGs to the README** under "Test evidence" with a one-line caption each, stop the HTTP server, commit (short message), push.

One red + one green **per layer** — not per individual test. `.playwright-mcp/` is scratch and git-ignored.

**Enforcement (mechanical, not just this rule):**
- `tools/check_evidence.py` fails if any `tests/test_<label>.py` lacks both screenshots referenced in the README.
- A **pre-commit hook** (`.githooks/pre-commit`) runs that check plus the full test suite, blocking any commit that violates either. Enable it once per clone: `git config core.hooksPath .githooks`. Bypass a deliberate WIP commit with `git commit --no-verify`.
- **CI** (`.github/workflows/ci.yml`) runs the same check and the tests on every push and PR — so a bypassed local commit still gets caught on GitHub.
- What the machine can't check: that the red was captured *live, before* the code. That stays on you and me to honor.

## Don't
- Don't invent assignment requirements Canvas doesn't hold. Surface what the instructor put in Canvas — nothing more.
- Don't add HTMX or new frameworks for the core build. Server-rendered Jinja2 is enough.
- Don't reimplement the proven Python engine in another language.
