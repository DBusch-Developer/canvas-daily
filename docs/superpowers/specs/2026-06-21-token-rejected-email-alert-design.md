# Email the user when a connection's token breaks

Date: 2026-06-21

## Problem

A Canvas access token can stop working *after* it was added — revoked,
expired, or regenerated in Canvas. Two things go wrong today:

1. **The daily sync has no per-connection error handling.** `run_daily_sync`
   loops connections and calls `sync_connection`, which raises on a Canvas
   `401`. The first broken connection aborts the entire run — so one student's
   dead token stops the daily fetch for **every** user, and `run_sync` never
   commits. No connection is ever marked `error` by the daily job (only the
   add-time background task does that).
2. **Nobody is told.** Even once a connection is in `error`, the user has no
   idea their report is now missing a class until they notice the gap.

We want the daily sync to **survive a single connection failing**, and to
**email the affected user once** — branded like the daily report — with clear
steps to issue a new token.

## Decisions

- **Per-connection resilience.** `run_daily_sync` wraps each connection: on
  success mark `sync_status="ok"`; on any failure mark `sync_status="error"`
  and continue to the next. One bad connection never aborts the run.
- **Classify the failure.** A *token rejection* is an `httpx.HTTPStatusError`
  whose `response.status_code` is `401` or `403`. Everything else (timeout,
  network error, `5xx`, parse error) is an *other* failure — still marked
  `error`, but it does **not** trigger the email (a "get a new token" message
  would be wrong for a Canvas outage).
- **Once per breakage.** Email only on the working → broken transition: when a
  connection that was **not already** `error` fails on a token rejection. Uses
  the existing `sync_status` (read before overwrite) — **no new column**. A
  fixed connection returns to `ok`; if it breaks again later, that's a new
  email.
- **`sync.py` stays pure.** `run_daily_sync` returns the list of connections
  that newly broke on a token rejection. It never imports the mailer or SMTP.
  `jobs.run_sync` (the composition root) does the email I/O.
- **Branded email, same look as the report.** `build_token_error_email`
  renders a new `token_error_email.html` — the Canvas Daily logo header, the
  pink→purple→blue gradient bar, an amber "ACTION NEEDED" chip, four numbered
  steps, a "Fix this connection" button, and the standard footer — plus a
  plain-text fallback. The token never appears in either body.
- **Scope to the daily run.** The email fires from `run_sync` (daily cron),
  not from the add-time background task (`web.run_connection_sync`), where the
  user is already watching the setup page. Entry-time rejection is handled by
  Layer 23.
- **Own README layer (Layer 24)** — its own `tests/test_tokenalert.py`, its own
  `tokenalert-red.png` / `tokenalert-green.png` (red captured live), its own
  README section.

## Where/when the user sees it

A real email to `connection.user.email`, sent by `python -m app.jobs sync`
(daily cron) the first morning a connection's token is rejected. Branded HTML
(logo header identical to the report email) with a plain-text alternative.

## Components

### `app/sync.py` — `run_daily_sync`

```
def run_daily_sync(session, client):
    """Sync every connection. One path for one connection and for four.

    Per-connection resilient: a single connection failing marks only that
    connection `error` and does not abort the rest. Returns the connections
    that *newly* broke on a Canvas token rejection (401/403) — i.e. were not
    already `error` — so the caller can notify their owners once.
    """
    newly_broken = []
    for connection in session.exec(select(Connection)).all():
        was_error = connection.sync_status == "error"
        try:
            sync_connection(session, connection, client)
            connection.sync_status = "ok"
        except Exception as exc:
            connection.sync_status = "error"
            if not was_error and _is_token_rejection(exc):
                newly_broken.append(connection)
        session.add(connection)
    session.flush()
    return newly_broken


def _is_token_rejection(exc):
    return (
        isinstance(exc, httpx.HTTPStatusError)
        and exc.response.status_code in (401, 403)
    )
```

`import httpx` is added to `sync.py`. `sync_connection` is unchanged (it still
raises; the catching moves to the loop). Note `sync_connection` currently
stamps `last_synced_at` and flushes on success — that stays.

### `app/mailer.py` — `build_token_error_email`

```
def build_token_error_email(connection, base_url):
    """Return (subject, text_body, html) telling a user one connection's token
    was rejected. The token is never included."""
    subject = f"Action needed: reconnect {connection.label} on Canvas Daily"
    text_body = (... plain-text version of the four steps, with the
                 {base_url}/connections link ...)
    html = _EMAIL_TEMPLATES.get_template("token_error_email.html").render(
        logo_url=f"{base_url}/static/logo.png",
        base_url=base_url,
        label=connection.label,
    )
    return subject, text_body, html
```

Reuses the existing `_EMAIL_TEMPLATES` Jinja environment and `send_email`.

### `app/templates/token_error_email.html`

The approved mockup, inline-styled for email clients: logo header, gradient
bar, amber "ACTION NEEDED" chip, headline "One of your Canvas connections needs
a new token", intro naming `{{ label }}`, a light card with four numbered steps
(open Account → Settings; **+ New Access Token**, purpose "Canvas Daily", set
the **expiration date as far in the future as Canvas allows — it's required**;
**Generate Token**, then **select the whole token and copy it manually** —
shown once, no copy button; remove and re-add the connection), a navy "Fix this
connection →" button to `{{ base_url }}/connections`, the "other connections
still working" line, and the standard footer.

### `app/jobs.py` — `run_sync`

```
def run_sync():
    engine = _engine()
    with Session(engine) as session, httpx.Client(timeout=30.0) as client:
        newly_broken = run_daily_sync(session, client)
        session.commit()
        _email_broken_connections(session, newly_broken)


def _email_broken_connections(session, connections):
    """Send one token-error email per newly-broken connection. Best-effort:
    a missing SMTP config or a single send failure never aborts the sync."""
    if not connections:
        return
    host = os.environ.get("SMTP_HOST")
    if not host:
        return
    base_url = os.environ.get("PUBLIC_BASE_URL", "https://canvas-daily.org")
    sender = os.environ.get("SMTP_FROM") or os.environ.get("SMTP_USERNAME")
    smtp = _connect_smtp()
    try:
        for connection in connections:
            subject, text_body, html = build_token_error_email(connection, base_url)
            try:
                send_email(smtp, sender, connection.user.email, subject, text_body, html=html)
            except Exception:
                pass  # never log the token; one failure must not block others
    finally:
        smtp.quit()
```

Imports `build_token_error_email` and `send_email` from `app.mailer`. The
`connection.user.email` relationship loads inside the open session.

## Data flow

```
cron: python -m app.jobs sync
  -> run_sync
       run_daily_sync(session, client) -> newly_broken (token-rejection transitions)
       session.commit()
       _email_broken_connections(session, newly_broken)
            for each: build_token_error_email -> send_email -> user's inbox
```

## Error handling

- One connection's failure marks only that connection `error`; the loop
  continues.
- Non-token failures are marked `error` but never emailed.
- Best-effort email: no `SMTP_HOST` → skip sending (sync already committed); a
  single send raising is swallowed so the rest still go. The token is never
  logged.

## Test plan (TDD — live red before the implementation)

**New Layer 24 — `tests/test_tokenalert.py`** (label `tokenalert`):

- **Resilience (Neon test branch):** two connections, the first's Canvas
  returns `401`, the second succeeds. After `run_daily_sync`: first is
  `error`, second is `ok` with its assignment stored, and the call returns
  without raising.
- **Newly-broken, token only:** `run_daily_sync` returns the connection that
  hit `401`; a connection already `error` that `401`s again is **not** in the
  list; a connection that fails with a non-token error (e.g. `500`) is marked
  `error` but is **not** returned.
- **Email builder (pure):** `build_token_error_email(connection, base_url)`
  returns a subject, a text body, and HTML; the HTML contains the connection
  label, the `New Access Token` steps, the logo URL, and the
  `{base_url}/connections` button; the access token string never appears in
  subject, text, or HTML.

Canvas is mocked at the transport boundary (`httpx.MockTransport`), same as the
other sync layers. Resilience tests use the `TEST_DATABASE_URL` skip guard; the
email-builder test is pure (build `User`/`Connection` objects in memory).

## Test evidence

New `tokenalert-red.png` / `tokenalert-green.png` and a "Layer 24 — email the
user when a connection's token breaks" README section (description → red →
green), red captured live via
`tools/run_to_html.py tokenalert-red tests/test_tokenalert.py` before
`run_daily_sync` returns anything or `build_token_error_email` exists.

## Out of scope

- **The already-broken connection (id=9).** It is already `error`, so the
  working → broken transition has passed; it will not be emailed. It needs the
  one-off hand-sent note or a separate backfill (not this layer).
- **Add-time failures.** The setup page already shows the error live; no email
  from `web.run_connection_sync`.
- **Non-token failures (outages).** Marked `error`, no email.
- **Reminders / escalation.** Exactly one email per breakage; no follow-ups.
- **In-app banner.** Email only for now.
