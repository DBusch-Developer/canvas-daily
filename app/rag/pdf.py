"""Extract text from a PDF's bytes with pypdf.

Course PDFs are arbitrary uploads; a malformed file must never crash a sync, so
any parse error yields an empty string and the caller skips that file.
"""

import io
import logging

from pypdf import PdfReader

logger = logging.getLogger(__name__)


def extract_pdf_text(data: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(data))
        return "\n\n".join((page.extract_text() or "") for page in reader.pages).strip()
    except Exception:
        logger.warning("PDF extraction failed for a %d-byte file", len(data))
        return ""
