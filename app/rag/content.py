"""Fetch a course's text sources from Canvas as plain document dicts.

Each function returns dicts shaped {source_type, title, canvas_url, raw_text},
with HTML sanitized via nh3 and every list endpoint following the Link header.
Canvas is mocked at the httpx boundary in tests; the client is injected and the
token is never logged.
"""

import logging

import httpx
import nh3

logger = logging.getLogger(__name__)

from app.canvas import _next_page
from app.rag.pdf import extract_pdf_text


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


def fetch_pdf_documents(base_url, token, course_id, client):
    files = _get_all(
        f"{base_url}/api/v1/courses/{course_id}/files",
        {"per_page": 100}, _headers(token), client,
    )
    docs = []
    for f in files:
        if f.get("content-type") != "application/pdf":
            continue
        try:
            # Canvas file URLs 302-redirect to a CDN; follow it or every PDF
            # (the real lecture slideshows) comes back as an empty 302 body.
            download = client.get(f.get("url"), headers=_headers(token),
                                  follow_redirects=True)
            download.raise_for_status()
            text = extract_pdf_text(download.content)
        except httpx.HTTPStatusError as exc:
            logger.warning("PDF download failed (%s): %s", exc.response.status_code,
                           f.get("display_name") or "unknown")
            continue
        except Exception:
            logger.warning("PDF extract failed: %s", f.get("display_name") or "unknown")
            continue
        docs.append({
            "source_type": "file_pdf",
            "title": f.get("display_name") or "PDF",
            "canvas_url": f.get("html_url") or "",
            "raw_text": text,
        })
    return docs
