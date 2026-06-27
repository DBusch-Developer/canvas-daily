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
