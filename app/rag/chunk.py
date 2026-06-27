"""Split sanitized course text into paragraph-sized chunks for full-text search.

Paragraphs (blank-line separated) are the natural retrieval unit. A paragraph
longer than max_chars is hard-split on word boundaries so no single chunk is
unwieldy. Pure function: no I/O, no mocks.
"""


def chunk_text(text: str, max_chars: int = 1000) -> list[str]:
    chunks: list[str] = []
    for para in text.split("\n\n"):
        para = para.strip()
        if not para:
            continue
        if len(para) <= max_chars:
            chunks.append(para)
        else:
            chunks.extend(_split_long(para, max_chars))
    return chunks


def _split_long(para: str, max_chars: int) -> list[str]:
    out, current = [], ""
    for word in para.split():
        candidate = word if not current else f"{current} {word}"
        if len(candidate) > max_chars and current:
            out.append(current)
            current = word
        else:
            current = candidate
    if current:
        out.append(current)
    return out
