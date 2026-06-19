# Responsive top-nav (hamburger) — design (Layer 20)

Date: 2026-06-19

## Problem

The top bar renders the logo plus three inline nav items (Dashboard / Account /
Log out) with no responsive behavior, so on a phone the desktop bar shows as-is —
and with the logo now 72px wide it overflows on narrow screens. There is no way
to collapse the nav on mobile.

## Goal

Collapse the nav into a **hamburger menu** on small screens; leave the desktop
layout unchanged.

## Non-goals

- No change to desktop (≥720px) — the nav stays inline exactly as today.
- No nav changes on the login/signup pages (they use the `gate` layout, not the
  top bar).
- No new framework; a few lines of vanilla JS, consistent with the existing
  modal.

## Behavior

- **Below 720px:** the three nav items are hidden; a **hamburger button** appears
  at the right of the bar. Tapping it toggles a **dropdown panel** that drops
  below the bar with Dashboard / Account / Log out stacked full-width. Tapping
  again (or tapping a link) closes it.
- **At/above 720px:** the hamburger is hidden and the nav shows inline, as now.

## Markup (`app/templates/base.html`)

The top bar gains a toggle button before the nav, the nav gets an id, and the
toggle wires to it via ARIA:

```html
<div class="topbar__inner">
  <a class="topbar__logo" ...>…</a>
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
    <a class="navlink" href="/">Dashboard</a>
    <a class="navlink" href="/connections">Account</a>
    <form method="post" action="/logout"><button …>Log out</button></form>
  </nav>
</div>
```

## Styling (`app/static/app.css`)

Mobile-first: the toggle shows and the nav is a hidden dropdown by default; a
`min-width: 720px` media query restores the desktop inline nav and hides the
toggle.

- `.topbar__inner { position: relative; }` (anchor for the dropdown).
- `.topbar__toggle` — visible by default, `margin-left: auto`, button reset,
  padding, `color: var(--navy)`.
- `.topbar__nav` (mobile) — `display: none`; when open
  (`.topbar__nav--open`) → `display: flex; flex-direction: column;`
  absolutely positioned below the bar (`position: absolute; top: 100%; left/right:
  0;`), `background: var(--surface)`, top border, padding, shadow.
- `@media (min-width: 720px)`: `.topbar__toggle { display: none; }` and
  `.topbar__nav` returns to the current inline row (static position, `margin-left:
  auto`, no panel background).

## Toggle (`app/static/nav.js`, loaded in `base.html`)

```javascript
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

The app is JS-enhanced throughout (HTMX, the AI modal), so a JS toggle is
consistent; the button carries proper `aria-expanded` for screen readers.

## Components touched

- `app/templates/base.html` — toggle button + nav id.
- `app/static/app.css` — toggle + mobile dropdown + desktop media query.
- `app/static/nav.js` — new; the toggle script, loaded in `base.html`.
- `tests/test_nav.py` — new enforced layer (label `nav`).
- `README.md` — new "Layer 20" test-evidence section.
- `docs/test-evidence/nav-red.png`, `nav-green.png`.

## Test plan — TDD, Layer 20

New file `tests/test_nav.py` (label `nav`). The testable surface is the rendered
markup; the responsive CSS + JS toggle are verified live in the browser at a
phone width (open and closed) — same as the AI modal's JS was. FastAPI TestClient
+ in-memory SQLite.

Tests:

- **Toggle present:** a logged-in page (the dashboard) renders the hamburger
  button (`class="topbar__toggle"`, `aria-expanded="false"`,
  `aria-controls="primary-nav"`).
- **Nav wired:** the nav has `id="primary-nav"` and still contains Dashboard /
  Account / Log out.

TDD order (honoring CLAUDE.md test-evidence rules):

1. Write the failing tests first (the toggle button / nav id don't exist yet).
2. Capture **red live, before any code**: `nav-red.png`.
3. Add the Layer 20 section to the README with the red screenshot.
4. Implement until green.
5. Capture **green**: `nav-green.png`, add to README.
6. Verify by eye (pytest red/green) and verify the menu in the browser at a phone
   width (open + closed). Run `check_evidence`, commit with the pre-commit hook,
   push.
