"""Split text into retrieval-sized chunks on natural boundaries.

Paragraph first, then sentence, then hard cut. Overlap is carried between
chunks so a rule that straddles a boundary is still findable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .extract import Section

_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'\[])")


@dataclass
class TextChunk:
    text: str
    heading: str = ""
    page: int | None = None


def _split_paragraphs(text: str) -> list[str]:
    parts = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    return parts or ([text.strip()] if text.strip() else [])


def _split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_RE.split(text) if s.strip()]


def _hard_wrap(text: str, size: int) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)]


def chunk_text(
    text: str,
    *,
    target: int = 1100,
    overlap: int = 150,
    min_size: int = 200,
) -> list[str]:
    """Greedy pack paragraphs up to `target` characters."""
    if not text.strip():
        return []

    units: list[str] = []
    for para in _split_paragraphs(text):
        if len(para) <= target:
            units.append(para)
            continue
        for sentence in _split_sentences(para):
            if len(sentence) <= target:
                units.append(sentence)
            else:
                units.extend(_hard_wrap(sentence, target))

    chunks: list[str] = []
    buf: list[str] = []
    size = 0
    for unit in units:
        if size and size + len(unit) + 1 > target:
            chunks.append("\n\n".join(buf).strip())
            if overlap > 0:
                tail = chunks[-1][-overlap:]
                # Resume at a word boundary so the overlap reads cleanly.
                cut = tail.find(" ")
                buf = [tail[cut + 1 :] if cut != -1 else tail]
                size = len(buf[0])
            else:
                buf, size = [], 0
        buf.append(unit)
        size += len(unit) + 1

    if buf:
        tail = "\n\n".join(buf).strip()
        if tail and (len(tail) >= min_size or not chunks):
            chunks.append(tail)
        elif tail and chunks:
            chunks[-1] = f"{chunks[-1]}\n\n{tail}"

    return [c for c in chunks if c.strip()]


def chunk_sections(
    sections: list[Section],
    *,
    target: int = 1100,
    overlap: int = 150,
) -> list[TextChunk]:
    """Chunk each section, keeping its heading and page attached."""
    out: list[TextChunk] = []
    for section in sections:
        for piece in chunk_text(section.text, target=target, overlap=overlap):
            out.append(TextChunk(text=piece, heading=section.heading, page=section.page))
    return out
