"""The wikilink graph.

This is the reason the project uses Obsidian rather than a folder of text
files. When the Director needs context for "the crew is meeting Hollow at the
Grid Point", flat search finds notes containing those words; the graph also
finds the fixer who introduced Hollow, the gang that owns the block, and the
open thread that made the meeting dangerous. Neighbourhood beats keyword.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path

from .note import Note, slugify
from .vault import Vault


@dataclass
class VaultGraph:
    """An in-memory index of notes and the links between them."""

    notes: dict[str, Note] = field(default_factory=dict)          # slug -> Note
    out_links: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    in_links: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    unresolved: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))

    # -- construction -------------------------------------------------------
    @classmethod
    def build(cls, vault: Vault) -> "VaultGraph":
        graph = cls()
        for note in vault.notes():
            graph.notes[slugify(note.title)] = note
            if note.path is not None:
                stem = slugify(note.path.stem)
                graph.notes.setdefault(stem, note)

        for slug, note in list(graph.notes.items()):
            for target in note.links:
                tslug = slugify(target)
                if tslug in graph.notes:
                    graph.out_links[slug].add(tslug)
                    graph.in_links[tslug].add(slug)
                else:
                    graph.unresolved[tslug].add(slug)
        return graph

    # -- lookups ------------------------------------------------------------
    def get(self, title: str) -> Note | None:
        return self.notes.get(slugify(title))

    def backlinks(self, title: str) -> list[Note]:
        slug = slugify(title)
        return [self.notes[s] for s in sorted(self.in_links.get(slug, ())) if s in self.notes]

    def forward_links(self, title: str) -> list[Note]:
        slug = slugify(title)
        return [self.notes[s] for s in sorted(self.out_links.get(slug, ())) if s in self.notes]

    def neighbours(self, title: str, hops: int = 1) -> list[Note]:
        """Everything within `hops` links, in breadth-first order.

        Links are followed in both directions -- a backlink is just as much a
        semantic relationship as a forward link, and often a more interesting
        one (who mentions this NPC?).
        """
        start = slugify(title)
        if start not in self.notes:
            return []
        seen = {start}
        frontier = deque([(start, 0)])
        out: list[Note] = []
        while frontier:
            slug, depth = frontier.popleft()
            if depth >= hops:
                continue
            adjacent = self.out_links.get(slug, set()) | self.in_links.get(slug, set())
            for nxt in sorted(adjacent):
                if nxt in seen or nxt not in self.notes:
                    continue
                seen.add(nxt)
                out.append(self.notes[nxt])
                frontier.append((nxt, depth + 1))
        return out

    def expand(self, titles: list[str], hops: int = 1) -> list[Note]:
        """Neighbourhood of a whole result set, de-duplicated."""
        seen: set[str] = {slugify(t) for t in titles}
        out: list[Note] = []
        for title in titles:
            for note in self.neighbours(title, hops):
                slug = slugify(note.title)
                if slug not in seen:
                    seen.add(slug)
                    out.append(note)
        return out

    # -- analysis -----------------------------------------------------------
    def unique_notes(self) -> list[Note]:
        """Distinct notes -- `notes` maps several slugs onto the same object."""
        seen: dict[int, Note] = {}
        for note in self.notes.values():
            seen.setdefault(id(note), note)
        return list(seen.values())

    def degree(self, title: str) -> int:
        slug = slugify(title)
        return len(self.out_links.get(slug, ())) + len(self.in_links.get(slug, ()))

    def hubs(self, limit: int = 15) -> list[tuple[str, int]]:
        """Most-connected notes: usually the factions and recurring NPCs."""
        scored = {note.title: self.degree(note.title) for note in self.unique_notes()}
        return sorted(scored.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]

    def orphans(self) -> list[Note]:
        return [n for n in self.unique_notes() if self.degree(n.title) == 0]

    def broken_links(self) -> list[tuple[str, list[str]]]:
        """Wikilinks pointing at notes that do not exist yet.

        The engine treats these as a to-do list: a dangling [[Hollow]] is an
        NPC the story has promised and not yet delivered.
        """
        return [
            (target, sorted(sources))
            for target, sources in sorted(self.unresolved.items())
        ]

    def of_kind(self, *kinds: str) -> list[Note]:
        wanted = set(kinds)
        return [n for n in self.unique_notes() if n.kind in wanted]

    def stats(self) -> dict[str, int]:
        unique = self.unique_notes()
        return {
            "notes": len(unique),
            "links": sum(len(v) for v in self.out_links.values()),
            "unresolved": len(self.unresolved),
            "orphans": len(self.orphans()),
        }
