# Verify the Canvas token at entry

Date: 2026-06-21

## Problem

A user added a connection whose Canvas access token was invalid (Canvas returns
`401 Invalid access token` — the stored token was a 14-character fragment, almost
certainly pasted incompletely). Today the add flow accepts any string, stores the
connection, and only discovers the bad token in the **background** sync, which
catches the exception, marks `sync_status="error"`, and logs a generic
token-free warning. The user sees a spinner settle into *"We couldn't finish
pulling your assignments… try removing and re-adding it"* — with no hint that the
**token** is the problem. The real reason (a rejected token vs. a Canvas outage)
is never surfaced.

## Decisions

- **Validate synchronously at entry, by asking Canvas.** A Canvas token can't be
  validated by format (lengths and shapes vary by Canvas version), so the only
  authoritative check is an authenticated call. On `POST /connections`, before
  saving anything, call `GET {base_url}/api/v1/users/self` and branch on the
  result.
- **Probe `/users/self`** — the lightest authenticated endpoint (one record, no
  pagination). It confirms Canvas accepts the token. (Personal access tokens
  carry the full user's permissions, so a token that authenticates but can't read
  courses is not a real concern for this flow.)
- **Three outcomes, three messages.** `ok` → save + background sync, exactly as
  today. `invalid` (401/403) → re-render the form with a token-specific message,
  nothing saved. `unreachable` (any other status, timeout, or network error) →
  re-render with a "couldn't reach Canvas" message, nothing saved. A student is
  never blamed for an outage or a mistyped base URL.
- **A bad token never becomes a stored `error` connection and never spins.** It
  fails fast, in the form, with a clear next step.
- **Own README layer (Layer 23).** New TDD'd behavior → its own
  `tests/test_verify.py`, its own `verify-red.png` / `verify-green.png` (red
  captured live, before the code), its own README "Layer 23" section.

## New unit — `verify_token` (app/canvas.py)

Same shape as `fetch_courses` — Canvas mocked at the httpx transport boundary,
client injected, token never logged.

```
def verify_token(base_url, token, client) -> str:
    """Probe Canvas with the token. Returns "ok" | "invalid" | "unreachable".

    "ok"          -> Canvas accepted the token (200)
    "invalid"     -> Canvas rejected the token (401/403)
    "unreachable" -> any other status, timeout, or network error
    """
    url = f"{base_url.rstrip('/')}/api/v1/users/self"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        response = client.get(url, headers=headers)
    except httpx.HTTPError:
        return "unreachable"
    if response.status_code == 200:
        return "ok"
    if response.status_code in (401, 403):
        return "invalid"
    return "unreachable"
```

`base_url.rstrip('/')` because connections store the domain with and without a
trailing slash (the failing connection had one). `httpx` is already imported in
`canvas.py`'s test boundary; add the import to the module.

## Handler change — `add_connection` (app/web.py)

Before creating the connection, build a client from the injected factory and
verify:

```
client = client_factory()
try:
    result = verify_token(base_url, access_token, client)
finally:
    client.close()

if result != "ok":
    message = (
        "Canvas rejected this access token. In Canvas, go to "
        "Account -> Settings, generate a new access token, and paste it again."
        if result == "invalid" else
        "We couldn't reach Canvas to verify this connection. Double-check the "
        "base URL and try again."
    )
    return TEMPLATES.TemplateResponse(
        request, "connection_new.html",
        {"error": message, "label": label, "base_url": base_url,
         "account_type": account_type},
        status_code=400,
    )
# result == "ok": unchanged from today -> save + schedule background sync.
```

The success path (create connection, commit, `background_tasks.add_task(...)`,
redirect `303` to `/connections/{id}/setup`) is **unchanged**. The token is never
logged in either branch.

## Template — `app/templates/connection_new.html`

The `{% if error %}` block already exists. Add value prefills so a rejected submit
keeps everything except the token:

- Label input: `value="{{ label or '' }}"`
- Base URL input: `value="{{ base_url or '' }}"`
- Account type `<select>`: mark the matching option `selected` (compare to
  `account_type`).
- Access token input: **no** prefill — the field hint already says we never
  display it again.

## Test plan (TDD — live red before the implementation)

**New Layer 23 — `tests/test_verify.py`** (label `verify`):

- `verify_token` returns `"ok"` on a mocked `200` (unit, mock Canvas).
- `verify_token` returns `"invalid"` on a mocked `401`.
- `verify_token` returns `"unreachable"` on an `httpx` timeout / network error.
- `POST /connections` with a token Canvas rejects (`401`) → response `400`, the
  token-specific message is in the body, **no** connection row is created, and
  **no** background sync is scheduled.
- `POST /connections` with a token Canvas accepts (`200`) → connection saved and
  background sync scheduled (existing behavior preserved), redirect to setup.

Handler tests use `TestClient` with `get_engine` and `get_canvas_client_factory`
overridden, exactly like `tests/test_setup.py`. The DB-backed tests skip unless
`TEST_DATABASE_URL` is set (same guard as the other setup-flow layers).

## Test evidence

New `verify-red.png` / `verify-green.png` and a "Layer 23 — verify the Canvas
token at entry" README section (description -> red -> green), red captured live
via `tools/run_to_html.py verify-red tests/test_verify.py` before `verify_token`
exists.

## Out of scope

- **A token that breaks later.** A connection whose token is revoked or expires
  *after* it worked still fails in the daily sync and shows the existing
  `error` status. Surfacing a reason for ongoing failures is a separate concern.
- No retry/backoff on `unreachable` — the user just resubmits.
- No format/length validation — Canvas is the only authority on token validity.
- No change to the background sync, the setup page, or the status endpoint.
