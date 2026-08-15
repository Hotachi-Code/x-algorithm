"""Turn plain prose into a linked graph.

Two passes. First, known setting vocabulary from `data/lexicon.json` (plus any
`lexicon.json` in the project root) is matched and wrapped in wikilinks.
Second, repeated capitalised phrases that look like proper nouns are proposed
as new entities -- that is how names out of *your* books get into the graph
without anybody typing them in.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from ..vaultio.note import slugify

DATA_LEXICON = Path(__file__).resolve().parent.parent / "data" / "lexicon.json"

# Words that start sentences and would otherwise look like proper nouns.
_STOP_CAPS = {
    "The", "A", "An", "This", "That", "These", "Those", "It", "If", "When",
    "While", "After", "Before", "Once", "Each", "Every", "Any", "All", "Some",
    "You", "Your", "They", "Their", "He", "She", "We", "Our", "His", "Her",
    "In", "On", "At", "For", "But", "And", "Or", "As", "By", "With", "From",
    "There", "Here", "What", "Which", "Who", "How", "Why", "Table", "Chapter",
    "Note", "Example", "Step", "See", "Roll", "Make", "Use", "Add", "Page",
}

_CAPS_PHRASE_RE = re.compile(
    r"\b([A-Z][a-z'’]+(?:[-\s][A-Z][a-z'’]+){0,3})\b"
)
_CODE_OR_LINK_RE = re.compile(r"(\[\[[^\]]*\]\]|`[^`]*`|```.*?```)", re.DOTALL)


def load_lexicon(extra: Path | None = None) -> dict[str, list]:
    """Load the bundled lexicon, merged with a project-local one if present.

    Entries stay in their raw form -- a plain string, or a
    `{"name": ..., "aliases": [...]}` mapping. `lexicon_entries` normalises
    them; `lexicon_names` gives just the canonical names.
    """
    data: dict[str, list] = {}
    for path in (DATA_LEXICON, extra):
        if path is None or not Path(path).is_file():
            continue
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        for key, values in raw.items():
            if key.startswith("_") or not isinstance(values, list):
                continue
            bucket = data.setdefault(key, [])
            known = {_entry_name(v) for v in bucket}
            for value in values:
                name = _entry_name(value)
                if name and name not in known:
                    known.add(name)
                    bucket.append(value)
    return data


def _entry_name(entry) -> str:
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        return str(entry.get("name", ""))
    return ""


def _entry_aliases(entry) -> list[str]:
    if isinstance(entry, dict):
        return [str(a) for a in entry.get("aliases", []) if str(a)]
    return []


def lexicon_entries(lexicon: dict[str, list]) -> list[tuple[str, list[str], str]]:
    """Flatten to (canonical name, aliases, kind) triples."""
    out: list[tuple[str, list[str], str]] = []
    for kind, values in lexicon.items():
        for value in values:
            name = _entry_name(value)
            if name:
                out.append((name, _entry_aliases(value), kind))
    return out


def lexicon_names(kind: str, extra: Path | None = None) -> list[str]:
    """Canonical names for one category -- what the generators should use."""
    return [_entry_name(v) for v in load_lexicon(extra).get(kind, []) if _entry_name(v)]


def is_linkable(term: str) -> bool:
    """Only proper nouns become notes.

    The test is simply whether any word is capitalised. That keeps 'fixer',
    'street samurai' and 'grid' out of the graph as notes while still letting
    'Renraku', 'the Matrix' and 'Redmond Barrens' in -- and it gives the user a
    one-keystroke way to promote a term: capitalise it in the lexicon.
    """
    return any(word[:1].isupper() for word in term.split())


@dataclass
class EntityLinker:
    """Matches known entities in text and wraps them in Obsidian wikilinks."""

    lexicon: dict[str, list[str]] = field(default_factory=load_lexicon)
    min_length: int = 4
    max_links_per_chunk: int = 12

    _pattern: re.Pattern | None = field(default=None, init=False, repr=False)
    _canonical: dict[str, str] = field(default_factory=dict, init=False, repr=False)
    _kinds: dict[str, str] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        self.compile()

    # -- setup --------------------------------------------------------------
    def compile(self) -> None:
        self._canonical.clear()
        self._kinds.clear()
        terms: list[str] = []
        for name, aliases, kind in lexicon_entries(self.lexicon):
            # Aliases resolve to the canonical note, so "Renraku" and "Renraku
            # Computer Systems" are one entity rather than two.
            for surface in (name, *aliases):
                if len(surface) < self.min_length or not is_linkable(surface):
                    continue
                key = surface.lower()
                if key in self._canonical:
                    continue
                self._canonical[key] = name
                self._kinds[key] = kind
                terms.append(surface)
        if not terms:
            self._pattern = None
            return
        # Longest first so "Renraku Computer Systems" wins over "Renraku".
        terms.sort(key=len, reverse=True)
        joined = "|".join(re.escape(t) for t in terms)
        # Lookarounds keep matches off word interiors and out of existing links.
        self._pattern = re.compile(
            rf"(?<![\w\[])({joined})(?![\w\]])", re.IGNORECASE
        )

    def add_terms(self, kind: str, terms: list[str]) -> None:
        bucket = self.lexicon.setdefault(kind, [])
        for term in terms:
            if term not in bucket:
                bucket.append(term)
        self.compile()

    # -- matching -----------------------------------------------------------
    def find(self, text: str) -> list[tuple[str, str]]:
        """Return (canonical name, kind) for every distinct entity in `text`."""
        if self._pattern is None:
            return []
        found: dict[str, str] = {}
        for match in self._pattern.finditer(text):
            key = match.group(1).lower()
            canonical = self._canonical.get(key)
            if canonical and canonical not in found:
                found[canonical] = self._kinds.get(key, "concept")
        return list(found.items())

    def link(self, text: str, *, limit: int | None = None) -> tuple[str, list[str]]:
        """Wrap entities in wikilinks, once each, skipping code and existing links."""
        if self._pattern is None:
            return text, []

        limit = self.max_links_per_chunk if limit is None else limit
        linked: list[str] = []
        seen: set[str] = set()

        def replace(match: re.Match) -> str:
            surface = match.group(1)
            canonical = self._canonical.get(surface.lower())
            if canonical is None or len(seen) >= limit:
                return surface
            # De-duplicate on the canonical name, not the surface form, so
            # "Renraku" and "Renraku Computer Systems" count as one link.
            if canonical in seen:
                return surface
            seen.add(canonical)
            linked.append(canonical)
            # Preserve the author's casing with an alias when it differs.
            if surface == canonical:
                return f"[[{canonical}]]"
            return f"[[{canonical}|{surface}]]"

        # Never rewrite inside existing links or code spans.
        parts = _CODE_OR_LINK_RE.split(text)
        for i, part in enumerate(parts):
            if i % 2 == 0:
                parts[i] = self._pattern.sub(replace, part)
        return "".join(parts), linked

    # -- discovery ----------------------------------------------------------
    def propose_entities(
        self, text: str, *, min_count: int = 3, limit: int = 40
    ) -> list[tuple[str, int]]:
        """Repeated capitalised phrases that are not already known.

        These are candidates, not conclusions -- `srgm ingest` writes them to a
        review note so a human decides what becomes canon.
        """
        counts: Counter[str] = Counter()
        for match in _CAPS_PHRASE_RE.finditer(text):
            phrase = match.group(1).strip()
            head = phrase.split()[0]
            if head in _STOP_CAPS and len(phrase.split()) == 1:
                continue
            if len(phrase) < self.min_length:
                continue
            if phrase.lower() in self._canonical:
                continue
            counts[phrase] += 1
        return [
            (phrase, count)
            for phrase, count in counts.most_common(limit)
            if count >= min_count
        ]

    def stub_notes(self, entities: list[tuple[str, str]]) -> list[tuple[str, str, str]]:
        """(title, kind, body) triples for entities with no note yet."""
        out = []
        for name, kind in entities:
            body = (
                f"> [!info] Auto-created from the corpus as a **{kind}**.\n"
                f"> Fill this in and it becomes part of the Director's context.\n\n"
                f"## Summary\n\n_TODO_\n\n"
                f"## Hooks\n\n- \n\n"
                f"## Appears in\n\n_Backlinks below._\n"
            )
            out.append((name, _kind_to_folder(kind), body))
        return out


def _kind_to_folder(kind: str) -> str:
    return {
        "megacorp": "faction",
        "faction": "faction",
        "place": "location",
        "critter": "setting",
        "metatype": "setting",
        "magic": "setting",
        "matrix": "setting",
        "gear": "setting",
        "concept": "setting",
        "role": "setting",
        "archetype": "setting",
    }.get(kind, "setting")


def dedupe_titles(titles: list[str]) -> list[str]:
    seen, out = set(), []
    for title in titles:
        slug = slugify(title)
        if slug not in seen:
            seen.add(slug)
            out.append(title)
    return out
