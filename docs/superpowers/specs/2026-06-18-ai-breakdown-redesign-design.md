# AI breakdown redesign — design (Layer 12)

Date: 2026-06-18

## Problem

The "Break this down with AI" feature has three problems:

- The button sits at the bottom of the detail page; you scroll past the meta grid
  and full instructions to reach it.
- The result renders as raw markdown in a `<pre>` monospace block, dumped even
  lower on the page.
- The content is a literal restatement of the assignment (What's being asked /
  Step-by-step plan / Watch out for / Time estimate), not the kind of head-start a
  student actually wants — where to research, how to structure the work, what
  angles to take.

## Goals

- Move the trigger to the top of the page, beside "Open in Canvas".
- Show the result in a modal, as styled section cards — never raw markdown.
- Replace the content with four research/planning-focused sections.
- Add a copy-to-clipboard button so a student can paste the breakdown straight
  into a Word document and start working.

## Non-goals

- No live Canvas calls on click (detail pages read from storage; AI is on-demand).
- No fabricated citations, sources, or URLs.
- No new web framework. HTMX is already in the stack; nothing else is added.

## 1. Placement

The trigger button moves into the `brief` section at the top of `detail.html`,
immediately after "Open in Canvas ↗". The old bottom CTA form and the trailing
`#breakdown-result` div are removed.

```
Mission brief
Essay 2: Industrial Revolution
[HIST 101]  [Open]

[ Open in Canvas ↗ ]   [ ✦ Break this down with AI ]

…meta grid + instructions panel stay below, untouched…
```

## 2. Presentation — modal

A native `<dialog>` element holds the result. The shell lives hidden in the DOM.

- Click the button → dialog opens showing a spinner.
- HTMX POSTs to the breakdown route, the rendered result swaps into the dialog
  body, and the dialog is shown (`dialog.showModal()`), triggered on
  `htmx:afterOnLoad`.
- Close via the ✕ button, the backdrop, or Esc.
- The result renders as four styled section cards (heading + body), not a `<pre>`.

```
╔══════════════════════════════════════╗
║ AI study breakdown      [Copy]   ✕   ║
║ ──────────────────────────────────── ║
║  ◆ What's being asked                ║
║  ◆ Where to start researching        ║
║  ◆ Outline of the work               ║
║  ◆ Ideas & angles                    ║
╚══════════════════════════════════════╝
```

### No-JS fallback (one code path)

Without JS/HTMX, the button is a normal form submit that POSTs to the breakdown
route and receives the full breakdown page (as today). With JS, the same response
swaps into the dialog. The route branches on the `HX-Request` header exactly as it
does now — one server code path, progressive enhancement on top.

## 3. Content — structured JSON

The model is asked for a JSON object with exactly four string fields, rendered as
cards by Jinja:

1. `whats_being_asked` — plain-language restatement, grounded in the real Canvas
   details.
2. `where_to_research` — research *directions*: source types, search terms,
   library databases. The prompt explicitly forbids inventing citations, source
   titles, or URLs.
3. `outline` — a skeleton of the deliverable (sections/headings/steps).
4. `ideas` — possible approaches, thesis angles, directions to explore.

Each field may contain simple line breaks / dash bullets; rendering splits on
lines and shows them as list items. All model text lands in auto-escaped Jinja
text nodes, so no raw HTML from the model ever reaches the page — the
sanitization rule is satisfied without a markdown library or an nh3 round-trip.

### Request details

- Groq JSON mode via `response_format={"type": "json_object"}`.
- Temperature stays 0.5.
- Combined cap raised from 300 to ~500 words (bounded so the modal stays scannable).
- The API key still rides in the `Authorization` header and is never logged.

### Failure handling

- Timeout → 504, clean "took too long" message rendered inside the modal.
- Any other error (including invalid/unparseable JSON from the model) → 502, clean
  "unavailable right now" message. A JSON parse failure is caught and turned into
  the same clean `AIError` path — never a broken page.

## 4. Copy to clipboard

A "Copy" button sits in the modal header. On click it copies a clean plain-text
rendering of all four sections (section titles as headings, body lines as bullets)
via `navigator.clipboard.writeText`, then briefly shows "Copied!". The plain-text
form is built so it pastes cleanly into Word. This is part of the JS-enhanced
layer; no-JS users get the full page and can select/copy manually.

## 5. Rule and docs updates

Because the richer content conflicts with the existing CLAUDE.md hard rule, the
user chose to expand the format and update the rule:

- Amend the CLAUDE.md hard rule about the four-section format to name the new four
  sections (What's being asked / Where to start researching / Outline of the work
  / Ideas & angles) and the new ~500-word cap.
- The AI breakdown stays on-demand, temperature 0.5, key from `GROQ_API_KEY`.

## Components touched

- `app/ai.py` — new system prompt, JSON request (`response_format`), parse the
  four fields, clean error on parse failure. `build_messages` updated.
- `app/web.py` — breakdown route passes parsed sections to the template; HX-Request
  branch unchanged in spirit.
- `app/templates/detail.html` — button moves to top; dialog shell added.
- `app/templates/breakdown_result.html` — renders four section cards + error.
- `app/templates/breakdown.html` — full-page fallback uses the same partial.
- `app/static/` — small CSS for dialog + cards; small JS for open/close + copy.
- `CLAUDE.md` — amend the four-section hard rule.
- `README.md` — new "Layer 12" test-evidence section.

## Test plan — TDD, Layer 12

New file `tests/test_breakdown.py` (its own enforced layer — distinct from the
existing `ai` layer). Label is `breakdown`, so the screenshots are
`breakdown-red.png` / `breakdown-green.png` (single-token label keeps
`check_evidence` happy — it requires `<label>-red.png` derived verbatim from the
filename). Groq is mocked at the httpx transport boundary, as in the existing AI
layer.

- `build_messages` includes the JSON instruction and the four section names.
- The request sends `response_format` json_object and temperature 0.5.
- A mocked JSON response is parsed into the four named fields.
- Missing assignment fields are still dropped from the context block.
- Invalid/non-JSON model output raises a clean `AIError` (→ 502), not a crash.
- Timeout raises `AITimeoutError` (→ 504).
- The API key never appears in logs.

TDD order, honoring CLAUDE.md test-evidence rules:

1. Write the failing tests first (implementation does not exist).
2. Capture **red live, before any code**: `breakdown-red.png`.
3. Add the Layer 12 section to the README with the red screenshot.
4. Implement until green.
5. Capture **green**: `breakdown-green.png`, add to README.
6. Verify both images by eye, run `check_evidence`, commit with the pre-commit
   hook, push.
