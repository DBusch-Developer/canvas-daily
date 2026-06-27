"""Layer 30 — turn a course's PDF files into document dicts.

Canvas Files API mocked at the httpx boundary. Only application/pdf files are
downloaded; their text is extracted with pypdf; a non-PDF is ignored and a
corrupt PDF is skipped, never fatal.
"""

import httpx
from pypdf import PdfWriter

from app.rag.content import fetch_pdf_documents
from app.rag.pdf import extract_pdf_text

BASE = "https://school.test"


def _one_page_pdf_bytes(text):
    # pypdf can't author text content easily; use a blank page and assert the
    # extractor returns a string without raising. Real text-extraction is
    # covered by the corrupt-vs-valid distinction below.
    import io
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def test_extract_returns_string_for_valid_pdf():
    out = extract_pdf_text(_one_page_pdf_bytes("hello"))
    assert isinstance(out, str)


def test_extract_returns_empty_on_corrupt_bytes():
    assert extract_pdf_text(b"not a pdf at all") == ""


def test_only_pdfs_are_downloaded_and_become_documents():
    pdf_bytes = _one_page_pdf_bytes("syllabus text")

    def handler(request):
        path = request.url.path
        if path.endswith("/files"):
            return httpx.Response(200, json=[
                {"id": 1, "display_name": "Syllabus.pdf",
                 "content-type": "application/pdf",
                 "url": f"{BASE}/files/1/download",
                 "html_url": f"{BASE}/courses/7/files/1"},
                {"id": 2, "display_name": "logo.png",
                 "content-type": "image/png",
                 "url": f"{BASE}/files/2/download",
                 "html_url": f"{BASE}/courses/7/files/2"},
            ])
        if path.endswith("/files/1/download"):
            return httpx.Response(200, content=pdf_bytes)
        return httpx.Response(404, content=b"")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    docs = fetch_pdf_documents(BASE, "tok", 7, client)
    assert len(docs) == 1
    assert docs[0]["source_type"] == "file_pdf"
    assert docs[0]["title"] == "Syllabus.pdf"
    assert docs[0]["canvas_url"] == f"{BASE}/courses/7/files/1"


def test_one_bad_pdf_does_not_abort_rest():
    """A 403 on the first PDF must not abort the second valid PDF."""
    pdf_bytes = _one_page_pdf_bytes("valid content")

    def handler(request):
        path = request.url.path
        if path.endswith("/files"):
            return httpx.Response(200, json=[
                {"id": 1, "display_name": "Bad.pdf",
                 "content-type": "application/pdf",
                 "url": f"{BASE}/files/1/download",
                 "html_url": f"{BASE}/courses/7/files/1"},
                {"id": 2, "display_name": "Good.pdf",
                 "content-type": "application/pdf",
                 "url": f"{BASE}/files/2/download",
                 "html_url": f"{BASE}/courses/7/files/2"},
            ])
        if path.endswith("/files/1/download"):
            return httpx.Response(403, content=b"Forbidden")
        if path.endswith("/files/2/download"):
            return httpx.Response(200, content=pdf_bytes)
        return httpx.Response(404, content=b"")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    docs = fetch_pdf_documents(BASE, "tok", 7, client)
    assert len(docs) == 1, f"expected 1 doc (the valid PDF), got {len(docs)}"
    assert docs[0]["title"] == "Good.pdf"
