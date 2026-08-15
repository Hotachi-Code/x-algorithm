"""A single Obsidian note: YAML frontmatter plus markdown body.

PyYAML is used when it is installed, but the fallback parser handles the
subset this project emits (scalars, inline lists, block lists) so a table can
run the whole thing with nothing but the standard library.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:  # pragma: no cover - exercised implicitly by whichever branch is live
    import yaml as _yaml
except Exception:  # pragma: no cover
    _yaml = None

FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?", re.DOTALL)
WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|([^\]]+))?\]\]")
CODE_RE = re.compile(r"```.*?```|`[^`\n]*`", re.DOTALL)
TAG_RE = re.compile(r"(?:^|\s)#([A-Za-z][\w/-]*)")

_SLUG_STRIP = re.compile(r"[^\w\s-]")
_SLUG_SPACE = re.compile(r"[\s_-]+")


def slugify(text: str, *, max_len: int = 80) -> str:
    """Filesystem-safe, Obsidian-friendly note name."""
    norm = unicodedata.normalize("NFKD", text)
    norm = norm.encode("ascii", "ignore").decode("ascii")
    norm = _SLUG_STRIP.sub("", norm).strip()
    norm = _SLUG_SPACE.sub("-", norm).lower()
    return norm[:max_len].strip("-") or "untitled"


# --------------------------------------------------------------------------
# frontmatter
# --------------------------------------------------------------------------

def _parse_scalar(raw: str) -> Any:
    text = raw.strip()
    if not text:
        return ""
    if text[0] in "\"'" and text[-1] == text[0] and len(text) > 1:
        return text[1:-1]
    low = text.lower()
    if low in ("true", "yes"):
        return True
    if low in ("false", "no"):
        return False
    if low in ("null", "~", "none"):
        return None
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    if re.fullmatch(r"-?\d*\.\d+", text):
        return float(text)
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(part) for part in _split_inline(inner)]
    return text


def _split_inline(inner: str) -> list[str]:
    """Split an inline list on commas that are not inside quotes or brackets."""
    parts, buf, depth, quote = [], [], 0, ""
    for ch in inner:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = ""
            continue
        if ch in "\"'":
            quote = ch
            buf.append(ch)
        elif ch in "[{":
            depth += 1
            buf.append(ch)
        elif ch in "]}":
            depth -= 1
            buf.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        parts.append("".join(buf))
    return [p.strip() for p in parts if p.strip()]


def _fallback_parse(text: str) -> dict[str, Any]:
    data: dict[str, Any] = {}
    key: str | None = None
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.lstrip().startswith("- ") and key is not None:
            data.setdefault(key, [])
            if isinstance(data[key], list):
                data[key].append(_parse_scalar(line.lstrip()[2:]))
            continue
        if ":" in line:
            raw_key, _, raw_val = line.partition(":")
            key = raw_key.strip()
            value = raw_val.strip()
            data[key] = [] if value == "" else _parse_scalar(value)
    return data


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split a note into (frontmatter dict, body)."""
    match = FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    raw = match.group(1)
    body = text[match.end():]
    if _yaml is not None:
        try:
            loaded = _yaml.safe_load(raw)
            if isinstance(loaded, dict):
                return loaded, body
        except Exception:
            pass
    return _fallback_parse(raw), body


def _dump_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if text == "" or re.search(r"[:#\[\]{}]|^\s|\s$", text):
        return '"' + text.replace('"', '\\"') + '"'
    return text


def dump_frontmatter(data: dict[str, Any]) -> str:
    """Emit stable, diff-friendly YAML. Key order is preserved on purpose."""
    lines = []
    for key, value in data.items():
        if isinstance(value, (list, tuple, set)):
            items = list(value)
            if not items:
                lines.append(f"{key}: []")
            else:
                lines.append(f"{key}:")
                lines.extend(f"  - {_dump_scalar(v)}" for v in items)
        elif isinstance(value, dict):
            lines.append(f"{key}:")
            lines.extend(f"  {k}: {_dump_scalar(v)}" for k, v in value.items())
        else:
            lines.append(f"{key}: {_dump_scalar(value)}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# note
# --------------------------------------------------------------------------

@dataclass
class Note:
    """One markdown file in the vault."""

    title: str
    body: str = ""
    meta: dict[str, Any] = field(default_factory=dict)
    path: Path | None = None

    # -- construction -------------------------------------------------------
    @classmethod
    def from_text(cls, text: str, path: Path | None = None) -> "Note":
        meta, body = parse_frontmatter(text)
        title = str(meta.get("title") or (path.stem if path else "Untitled"))
        return cls(title=title, body=body, meta=meta, path=path)

    @classmethod
    def read(cls, path: Path) -> "Note":
        return cls.from_text(path.read_text(encoding="utf-8"), path=path)

    # -- serialisation ------------------------------------------------------
    def to_text(self) -> str:
        meta = dict(self.meta)
        meta.setdefault("title", self.title)
        fm = dump_frontmatter(meta)
        body = self.body.strip("\n")
        return f"---\n{fm}\n---\n\n{body}\n"

    def write(self, path: Path | None = None) -> Path:
        target = path or self.path
        if target is None:
            raise ValueError(f"note {self.title!r} has no path to write to")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self.to_text(), encoding="utf-8")
        self.path = target
        return target

    # -- content ------------------------------------------------------------
    @property
    def links(self) -> list[str]:
        """Outgoing wikilink targets, de-duplicated, order preserved.

        Code spans and fenced blocks are stripped first: documentation that
        shows `[[Name]]` as an example is not making a link.
        """
        seen, out = set(), []
        body = CODE_RE.sub(" ", self.body)
        for target, _alias in WIKILINK_RE.findall(body):
            name = target.strip()
            if name and name not in seen:
                seen.add(name)
                out.append(name)
        return out

    @property
    def tags(self) -> list[str]:
        meta_tags = self.meta.get("tags") or []
        if isinstance(meta_tags, str):
            meta_tags = [meta_tags]
        inline = TAG_RE.findall(self.body)
        seen, out = set(), []
        for tag in [*meta_tags, *inline]:
            tag = str(tag).lstrip("#")
            if tag and tag not in seen:
                seen.add(tag)
                out.append(tag)
        return out

    @property
    def kind(self) -> str:
        return str(self.meta.get("type") or "note")

    def append(self, text: str) -> None:
        self.body = f"{self.body.rstrip()}\n\n{text.strip()}\n"

    def upsert_section(self, heading: str, content: str, level: int = 2) -> None:
        """Replace a `## heading` section, or add it if it is missing.

        This is what lets the engine keep a note's Status block current across
        a hundred sessions without ever clobbering the prose above it.
        """
        marker = f"{'#' * level} {heading}"
        pattern = re.compile(
            rf"^{re.escape(marker)}\s*$.*?(?=^#{{1,{level}}} |\Z)",
            re.MULTILINE | re.DOTALL,
        )
        block = f"{marker}\n\n{content.strip()}\n\n"
        if pattern.search(self.body):
            self.body = pattern.sub(block, self.body, count=1)
        else:
            self.body = f"{self.body.rstrip()}\n\n{block}"

    def excerpt(self, limit: int = 320) -> str:
        text = re.sub(r"^#{1,6} .*$", "", self.body, flags=re.MULTILINE)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:limit] + ("..." if len(text) > limit else "")
