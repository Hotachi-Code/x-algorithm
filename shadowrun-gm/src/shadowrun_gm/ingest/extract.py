"""Pull readable text out of whatever the corpus folder contains.

Supported without any third-party package: .md, .txt, .html, .epub, .json.
PDFs need `pypdf` (pip install 'shadowrun-gm[ingest]'), because there is no
stdlib way to do it.
"""

from __future__ import annotations

import html
import json
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

TEXT_SUFFIXES = {".md", ".markdown", ".txt", ".text"}
HTML_SUFFIXES = {".html", ".htm", ".xhtml"}
SUPPORTED = TEXT_SUFFIXES | HTML_SUFFIXES | {".pdf", ".epub", ".json"}

_SCRIPT_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)
_BLOCK_RE = re.compile(r"</?(p|div|br|li|tr|h[1-6]|section|article)[^>]*>", re.IGNORECASE)
_HEAD_RE = re.compile(r"<h([1-6])[^>]*>(.*?)</h\1>", re.DOTALL | re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\xa0]+")
_BLANKS_RE = re.compile(r"\n{3,}")

# A heading in extracted book text: an ALL CAPS line, a numbered heading, or a
# short Title Case line with no terminal punctuation.
_HEADING_PATTERNS = (
    re.compile(r"^\s*(?:CHAPTER|PART|APPENDIX|SECTION)\s+[\dIVXLC]+\b.*$", re.IGNORECASE),
    re.compile(r"^\s*\d+(?:\.\d+)*\s+[A-Z][^.!?]{2,70}$"),
    re.compile(r"^\s*[A-Z][A-Z0-9 '&/,\-:]{3,60}$"),
)


@dataclass
class Section:
    """A titled run of text inside a document."""

    heading: str
    text: str
    page: int | None = None

    def is_empty(self) -> bool:
        return not self.text.strip()


@dataclass
class ExtractedDoc:
    title: str
    path: Path
    sections: list[Section] = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    @property
    def text(self) -> str:
        return "\n\n".join(f"{s.heading}\n{s.text}".strip() for s in self.sections)

    def word_count(self) -> int:
        return sum(len(s.text.split()) for s in self.sections)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def clean_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("­", "")            # soft hyphens from PDFs
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)  # de-hyphenate across lines
    text = _WS_RE.sub(" ", text)
    text = _BLANKS_RE.sub("\n\n", text)
    return text.strip()


def html_to_text(raw: str) -> str:
    raw = _SCRIPT_RE.sub(" ", raw)
    raw = _HEAD_RE.sub(lambda m: f"\n\n{'#' * int(m.group(1))} {m.group(2)}\n\n", raw)
    raw = _BLOCK_RE.sub("\n", raw)
    raw = _TAG_RE.sub(" ", raw)
    return clean_text(html.unescape(raw))


def looks_like_heading(line: str) -> bool:
    stripped = line.strip()
    if not (3 <= len(stripped) <= 80):
        return False
    if stripped.endswith((".", ",", ";", ":")):
        return False
    return any(p.match(stripped) for p in _HEADING_PATTERNS)


def split_into_sections(text: str, default_heading: str = "") -> list[Section]:
    """Break a wall of text into sections at detected headings."""
    sections: list[Section] = []
    heading = default_heading
    buf: list[str] = []

    def flush() -> None:
        body = clean_text("\n".join(buf))
        if body:
            sections.append(Section(heading=heading or default_heading, text=body))

    for line in text.splitlines():
        md = re.match(r"^(#{1,6})\s+(.*)$", line.strip())
        if md:
            flush()
            buf = []
            heading = md.group(2).strip()
            continue
        if looks_like_heading(line):
            # A heading before any body text simply names the first section.
            if any(b.strip() for b in buf):
                flush()
            buf = []
            heading = line.strip()
            continue
        buf.append(line)
    flush()
    return [s for s in sections if not s.is_empty()]


# --------------------------------------------------------------------------
# per-format extractors
# --------------------------------------------------------------------------

def _extract_text_file(path: Path) -> list[Section]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    return split_into_sections(raw, default_heading=path.stem)


def _extract_html_file(path: Path) -> list[Section]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    return split_into_sections(html_to_text(raw), default_heading=path.stem)


def _extract_json_file(path: Path) -> list[Section]:
    """Accept a list of {heading, text} records or a {heading: text} mapping."""
    data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    sections: list[Section] = []
    if isinstance(data, dict):
        for key, value in data.items():
            sections.append(Section(heading=str(key), text=clean_text(_stringify(value))))
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                heading = str(item.get("heading") or item.get("title") or path.stem)
                body = item.get("text") or item.get("body") or ""
                sections.append(Section(heading=heading, text=clean_text(_stringify(body))))
            else:
                sections.append(Section(heading=path.stem, text=clean_text(str(item))))
    return [s for s in sections if not s.is_empty()]


def _stringify(value: object) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, indent=2, ensure_ascii=False)


def _extract_epub(path: Path) -> list[Section]:
    """Read an EPUB straight out of its zip container -- no dependency needed."""
    sections: list[Section] = []
    with zipfile.ZipFile(path) as zf:
        names = [
            n for n in zf.namelist()
            if n.lower().endswith((".xhtml", ".html", ".htm"))
        ]
        for name in sorted(names):
            try:
                raw = zf.read(name).decode("utf-8", errors="replace")
            except KeyError:
                continue
            text = html_to_text(raw)
            if len(text) < 40:
                continue
            sections.extend(
                split_into_sections(text, default_heading=Path(name).stem)
            )
    return sections


def _extract_pdf(path: Path) -> list[Section]:
    try:
        from pypdf import PdfReader  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise RuntimeError(
            f"reading {path.name} needs pypdf: pip install 'shadowrun-gm[ingest]'"
        ) from exc

    reader = PdfReader(str(path))
    sections: list[Section] = []
    for number, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception:
            continue
        text = clean_text(text)
        if len(text) < 40:
            continue
        page_sections = split_into_sections(text, default_heading=f"p.{number}")
        for section in page_sections:
            section.page = number
        sections.extend(page_sections)
    return sections


_EXTRACTORS = {
    ".pdf": _extract_pdf,
    ".epub": _extract_epub,
    ".json": _extract_json_file,
}


def extract_file(path: Path) -> ExtractedDoc | None:
    """Extract one file, or return None if the suffix is not supported."""
    suffix = path.suffix.lower()
    if suffix in TEXT_SUFFIXES:
        sections = _extract_text_file(path)
    elif suffix in HTML_SUFFIXES:
        sections = _extract_html_file(path)
    elif suffix in _EXTRACTORS:
        sections = _EXTRACTORS[suffix](path)
    else:
        return None

    sections = [s for s in sections if len(s.text.split()) >= 8]
    if not sections:
        return None

    title = re.sub(r"[_-]+", " ", path.stem).strip()
    return ExtractedDoc(
        title=title,
        path=path,
        sections=sections,
        meta={"format": suffix.lstrip("."), "sections": len(sections)},
    )


def iter_corpus(root: Path) -> Iterator[Path]:
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name.startswith("."):
            continue
        # The corpus README is this project's own instructions, not source material.
        if path.parent == root and path.name.lower() in ("readme.md", "readme.txt"):
            continue
        if path.suffix.lower() in SUPPORTED:
            yield path


def extract_corpus(root: Path) -> Iterator[tuple[Path, ExtractedDoc | None, str]]:
    """Yield (path, doc, error) for every candidate file under `root`."""
    for path in iter_corpus(root):
        try:
            yield path, extract_file(path), ""
        except Exception as exc:  # keep going: one bad PDF must not stop ingest
            yield path, None, str(exc)
