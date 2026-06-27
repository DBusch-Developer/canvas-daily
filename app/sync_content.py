"""Sync one course's Canvas content into the RAG store, on demand.

Fetch every source, sanitize, chunk, and replace this course's documents and
chunks so a re-sync is clean. Resilient per source: a failing page or PDF is
logged and skipped, never aborting the rest. One code path for one course or
many.
"""

import logging

from sqlmodel import select

from app.canvas import fetch_assignments
from app.models import CourseDocument, DocumentChunk, _utcnow
from app.rag.chunk import chunk_text
from app.rag.content import (
    fetch_announcements,
    fetch_module_items,
    fetch_pages,
    fetch_pdf_documents,
    fetch_syllabus,
)

logger = logging.getLogger(__name__)


def _assignment_documents(base_url, token, course_id, client):
    docs = []
    for a in fetch_assignments(base_url, token, course_id, client):
        text = (a.get("description") or "").strip()
        if not text:
            continue
        docs.append({
            "source_type": "assignment",
            "title": a.get("name") or "Assignment",
            "canvas_url": a.get("html_url") or "",
            "raw_text": text,
        })
    return docs


def _gather(base_url, token, canvas_course_id, client):
    sources = [
        ("syllabus", lambda: [d for d in [fetch_syllabus(
            base_url, token, canvas_course_id, client)] if d]),
        ("pages", lambda: fetch_pages(base_url, token, canvas_course_id, client)),
        ("modules", lambda: fetch_module_items(
            base_url, token, canvas_course_id, client)),
        ("assignments", lambda: _assignment_documents(
            base_url, token, canvas_course_id, client)),
        ("announcements", lambda: fetch_announcements(
            base_url, token, canvas_course_id, client)),
        ("pdfs", lambda: fetch_pdf_documents(
            base_url, token, canvas_course_id, client)),
    ]
    docs = []
    for label, fn in sources:
        try:
            docs.extend(fn())
        except Exception:
            logger.warning("course-content source %s failed; skipping", label)
    return docs


def sync_course_content(session, connection, course, client):
    docs = _gather(
        connection.base_url, connection.access_token,
        course.canvas_course_id, client,
    )

    # Replace this course's content so a re-sync is clean.
    for old in session.exec(
        select(CourseDocument).where(CourseDocument.course_id == course.id)
    ).all():
        session.delete(old)
    session.flush()

    for d in docs:
        document = CourseDocument(
            course_id=course.id,
            source_type=d["source_type"],
            title=d["title"],
            canvas_url=d["canvas_url"],
            raw_text=d["raw_text"],
        )
        session.add(document)
        session.flush()
        for piece in chunk_text(d["raw_text"]):
            session.add(DocumentChunk(
                course_id=course.id,
                document_id=document.id,
                chunk_text=piece,
                source_title=d["title"],
                source_url=d["canvas_url"],
            ))

    course.last_content_synced_at = _utcnow()
    session.add(course)
    session.flush()
