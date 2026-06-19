# Responsive Top-Nav (Hamburger) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collapse the top nav into a hamburger dropdown below 720px; leave desktop unchanged.

**Architecture:** A hamburger `<button>` in the top bar (hidden on desktop via CSS) toggles an `open` class on the nav (mobile dropdown); a `min-width: 720px` media query restores the inline desktop nav. A few lines of vanilla JS handle the toggle with `aria-expanded`.

**Tech Stack:** Jinja2 (base layout), CSS media query, small vanilla JS, pytest (FastAPI TestClient + in-memory SQLite).

## Global Constraints

- TDD-first, red before green. New behavior is its own layer `tests/test_nav.py` (label `nav`).
- Evidence: `docs/test-evidence/nav-red.png` / `nav-green.png`, both in README as **Layer 20**. Red captured live before any implementation. Both PNGs committed together at the end.
- Desktop (≥720px) unchanged. Hamburger only below 720px.
- Login/signup unaffected (gate layout, no top bar).
- No new framework.
- Commit with the pre-commit hook. Short messages. Commit on `main`, no branches.
- Run pytest via the venv: `.venv/Scripts/python.exe -m pytest ...`.

---

## File Structure

- `tests/test_nav.py` — **create.** Layer 20 test: dashboard renders the toggle + nav markup.
- `app/templates/base.html` — **modify.** Add the toggle button; give the nav an id.
- `app/static/app.css` — **modify.** Toggle + mobile dropdown + `min-width: 720px` restore.
- `app/static/nav.js` — **create.** Toggle script.
- `README.md` — **modify.** Add Layer 20.

---

## Task 1: Write the failing Layer 20 test and capture RED

**Files:**
- Create: `tests/test_nav.py`
- Capture: `docs/test-evidence/nav-red.png`
- Modify: `README.md`

**Interfaces:**
- Consumes (does not exist yet): the `.topbar__toggle` button and `id="primary-nav"` in the rendered top bar.
- Produces: the enforced `nav` layer.

- [ ] **Step 1: Write `tests/test_nav.py`**

```python
"""Layer 20 - responsive top-nav (hamburger).

The top bar gains a hamburger toggle that collapses the nav on small screens.
Here we pin the rendered markup (toggle button + wired nav); the responsive CSS
and the JS toggle are verified live in the browser at phone width. FastAPI
TestClient + in-memory SQLite.
"""

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import User
from app.web import create_app, get_session


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
    from fastapi.testclient import TestClient
    return TestClient(app)


def test_topbar_has_hamburger_toggle(client, engine):
    client.post("/signup", data={"email": "nav@x.com", "password": "hunter2pw"},
                follow_redirects=False)
    resp = client.get("/")
    assert resp.status_code == 200
    assert 'class="topbar__toggle"' in resp.text
    assert 'aria-expanded="false"' in resp.text
    assert 'aria-controls="primary-nav"' in resp.text


def test_topbar_nav_is_wired_and_keeps_links(client, engine):
    client.post("/signup", data={"email": "nav2@x.com", "password": "hunter2pw"},
                follow_redirects=False)
    resp = client.get("/")
    assert resp.status_code == 200
    assert 'id="primary-nav"' in resp.text
    assert "Dashboard" in resp.text
    assert "Account" in resp.text
    assert "Log out" in resp.text
```

- [ ] **Step 2: Run the layer to confirm it is RED**

Run: `.venv/Scripts/python.exe tools/run_to_html.py nav-red tests/test_nav.py`
Expected: `[RED ...]`. Both tests fail — the top bar has no `topbar__toggle` button and the nav has no `id="primary-nav"`.

- [ ] **Step 3: Screenshot the red page**

Serve: `.venv/Scripts/python.exe -m http.server 8731 --directory docs/test-evidence` (background). Navigate to `http://127.0.0.1:8731/nav-red.html`, screenshot `.frame` to `nav-red.png`, move into `docs/test-evidence/`. Stop the server. Verify legible.

- [ ] **Step 4: Add the Layer 20 README section**

Insert after the Layer 19 block, before "How these are made":

```markdown
**Layer 20 — responsive top-nav (hamburger)**

The top bar had no responsive behavior, so on a phone the desktop nav showed as-is and (with the bigger logo) overflowed. A hamburger toggle now appears below 720px and drops the Dashboard / Account / Log out links into a panel below the bar; at 720px and up the nav stays inline as before. The toggle is a real `<button>` with `aria-expanded`, flipped by a few lines of vanilla JS.

Red — the toggle button and wired nav don't exist yet:

![Mobile-nav tests failing](docs/test-evidence/nav-red.png)

Green — after adding the toggle, the dropdown CSS, and the JS:

![Mobile-nav tests passing](docs/test-evidence/nav-green.png)
```

- [ ] **Step 5: Do NOT commit yet.** Both PNGs commit together at the end (Task 4).

---

## Task 2: Markup — toggle button and nav id (`app/templates/base.html`)

**Files:**
- Modify: `app/templates/base.html`
- Test: `tests/test_nav.py`

**Interfaces:**
- Produces: `.topbar__toggle` button (`aria-expanded`, `aria-controls="primary-nav"`); `<nav id="primary-nav">`.

- [ ] **Step 1: Add the toggle and id**

Change:

```html
      <a class="topbar__logo" href="/" aria-label="Canvas Daily home">
        <img src="/static/logo.png" alt="Canvas Daily">
      </a>
      <nav class="topbar__nav" aria-label="Primary">
```
to:
```html
      <a class="topbar__logo" href="/" aria-label="Canvas Daily home">
        <img src="/static/logo.png" alt="Canvas Daily">
      </a>
      <button class="topbar__toggle" type="button" aria-label="Menu"
              aria-expanded="false" aria-controls="primary-nav">
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none"
             stroke="currentColor" stroke-width="2" aria-hidden="true">
          <line x1="3" y1="6" x2="21" y2="6"/>
          <line x1="3" y1="12" x2="21" y2="12"/>
          <line x1="3" y1="18" x2="21" y2="18"/>
        </svg>
      </button>
      <nav class="topbar__nav" id="primary-nav" aria-label="Primary">
```

- [ ] **Step 2: Load the toggle script**

Below the existing `<script src="/static/breakdown.js"></script>` line, add:

```html
  <script src="/static/nav.js"></script>
```

- [ ] **Step 3: Run the test**

Run: `.venv/Scripts/python.exe -m pytest tests/test_nav.py -v`
Expected: both tests PASS.

---

## Task 3: Styling and toggle behavior

**Files:**
- Modify: `app/static/app.css`
- Create: `app/static/nav.js`

**Interfaces:**
- Consumes: `.topbar__toggle`, `#primary-nav`, `.topbar__nav--open`.

- [ ] **Step 1: Mobile-first nav CSS (`app/static/app.css`)**

Replace the current nav block:

```css
.topbar__nav {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: .35rem;
}
.topbar__nav form { display: inline; margin: 0; }
```
with:
```css
.topbar__inner { position: relative; }

.topbar__toggle {
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: .4rem;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--surface);
  color: var(--navy);
  cursor: pointer;
}

.topbar__nav {
  display: none;
  position: absolute;
  top: 100%;
  right: 0;
  left: 0;
  flex-direction: column;
  align-items: stretch;
  gap: .2rem;
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  box-shadow: var(--shadow);
  padding: .6rem 24px 1rem;
  z-index: 20;
}
.topbar__nav--open { display: flex; }
.topbar__nav form { display: block; margin: 0; }
.topbar__nav .btn { width: 100%; }

@media (min-width: 720px) {
  .topbar__toggle { display: none; }
  .topbar__nav {
    display: flex;
    position: static;
    flex-direction: row;
    align-items: center;
    margin-left: auto;
    gap: .35rem;
    background: none;
    border: 0;
    box-shadow: none;
    padding: 0;
  }
  .topbar__nav form { display: inline; }
  .topbar__nav .btn { width: auto; }
}
```

- [ ] **Step 2: Create `app/static/nav.js`**

```javascript
// Top-bar hamburger toggle (mobile). Desktop nav shows inline via CSS.
(function () {
  var toggle = document.querySelector(".topbar__toggle");
  var nav = document.getElementById("primary-nav");
  if (!toggle || !nav) return;
  toggle.addEventListener("click", function () {
    var open = nav.classList.toggle("topbar__nav--open");
    toggle.setAttribute("aria-expanded", open ? "true" : "false");
  });
})();
```

- [ ] **Step 3: Manually verify in the browser at phone width**

After capturing green (Task 4), serve a harness or the app and, at ~375px wide:
the hamburger shows, the inline links are hidden, tapping the hamburger drops the
panel (Dashboard / Account / Log out stacked), tapping again closes it; at ≥720px
the inline nav returns and the hamburger disappears. (CSS/JS aren't under pytest;
this is the visual check.)

- [ ] **Step 4: Run the layer**

Run: `.venv/Scripts/python.exe -m pytest tests/test_nav.py -v`
Expected: both tests still PASS (markup unchanged by the CSS/JS).

---

## Task 4: Capture GREEN, verify, commit

**Files:**
- Capture: `docs/test-evidence/nav-green.png`

- [ ] **Step 1: Render the green page**

Run: `.venv/Scripts/python.exe tools/run_to_html.py nav-green tests/test_nav.py`
Expected: `[GREEN (all passed)]`.

- [ ] **Step 2: Screenshot the green page**

Serve, navigate to `http://127.0.0.1:8731/nav-green.html`, screenshot `.frame` to `nav-green.png`, move into `docs/test-evidence/`. Stop the server. Verify legible.

- [ ] **Step 3: Verify the menu in the browser at phone width**

Build a harness with the real top bar markup + `app.css` + `nav.js`, set the
viewport to ~375px, screenshot closed and open. Confirm: hamburger visible, links
hidden until tapped, panel drops on tap; then resize to ≥720px and confirm the
inline nav returns. Remove the scratch harness/screenshots after.

- [ ] **Step 4: Run the full suite and evidence check**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: all pass.
Run: `.venv/Scripts/python.exe tools/check_evidence.py`
Expected: `OK - ... nav ...`.

- [ ] **Step 5: Commit and push**

```bash
git add app/ tests/test_nav.py docs/test-evidence/nav-red.png docs/test-evidence/nav-green.png docs/test-evidence/nav-red.html docs/test-evidence/nav-green.html README.md
git commit -m "Collapse the top nav into a hamburger menu on mobile (Layer 20)"
git push origin main
```
Expected: `[pre-commit] ok`, full suite passes, push succeeds.

---

## Self-Review

**Spec coverage:**
- Hamburger toggle markup → Task 2; asserted by `test_topbar_has_hamburger_toggle`.
- Nav wired (id + links) → Task 2; asserted by `test_topbar_nav_is_wired_and_keeps_links`.
- Mobile dropdown + desktop restore CSS → Task 3 Step 1.
- JS toggle → Task 3 Step 2.
- Desktop unchanged at ≥720px → Task 3 Step 1 media query; visual check Task 4 Step 3.
- Evidence (red live, green) → Task 1 (red) + Task 4 (green); both committed in Task 4.

**Placeholder scan:** No TBD/TODO; full code in every step; commands have expected output.

**Type consistency:** The class names `.topbar__toggle`, `#primary-nav`, `.topbar__nav--open` match across the template, CSS, JS, and tests. `aria-controls="primary-nav"` matches the nav `id`.

**Coupling to verify during execution:** The top bar lives in `base.html`'s `{% block layout %}`, which login/signup override — so the hamburger only appears on logged-in pages, and the test seeds a session via `/signup` then hits `/`. `nav.js` guards on the elements existing, so it is inert on pages without the top bar. The `var(--radius-sm)` / `var(--shadow)` tokens used already exist in `app.css`.
