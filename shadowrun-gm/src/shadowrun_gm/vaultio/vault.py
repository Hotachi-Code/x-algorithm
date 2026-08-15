"""The vault: a directory of notes, addressed by title."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

from .note import Note, slugify

# Folder layout. The numeric prefixes keep Obsidian's file explorer in a sane
# reading order: rules, then world, then the living campaign, then the crew.
FOLDERS = {
    "system": "00-System",
    "source": "05-Sources",
    "setting": "10-Setting",
    "faction": "10-Setting/Factions",
    "location": "10-Setting/Locations",
    "campaign": "20-Campaign",
    "thread": "20-Campaign/Threads",
    "scene": "20-Campaign/Scenes",
    "session": "20-Campaign/Sessions",
    "npc": "20-Campaign/NPCs",
    "runner": "30-Runners",
    "template": "90-Templates",
    "meta": "99-Meta",
}


@dataclass
class Vault:
    root: Path

    def __post_init__(self) -> None:
        self.root = Path(self.root)

    # -- layout -------------------------------------------------------------
    def ensure(self) -> None:
        for folder in FOLDERS.values():
            (self.root / folder).mkdir(parents=True, exist_ok=True)

    def folder_for(self, kind: str) -> Path:
        return self.root / FOLDERS.get(kind, FOLDERS["campaign"])

    def path_for(self, title: str, kind: str = "campaign") -> Path:
        return self.folder_for(kind) / f"{slugify(title)}.md"

    # -- iteration ----------------------------------------------------------
    def paths(self) -> Iterator[Path]:
        for path in sorted(self.root.rglob("*.md")):
            if any(part.startswith(".") for part in path.parts):
                continue
            yield path

    def notes(self) -> Iterator[Note]:
        for path in self.paths():
            try:
                yield Note.read(path)
            except OSError:
                continue

    def notes_of_kind(self, *kinds: str) -> list[Note]:
        wanted = set(kinds)
        return [n for n in self.notes() if n.kind in wanted]

    # -- addressing ---------------------------------------------------------
    def resolve(self, title: str) -> Path | None:
        """Find a note by title, slug, or filename -- the way Obsidian would."""
        target = slugify(title)
        for path in self.paths():
            if path.stem == title or slugify(path.stem) == target:
                return path
        for note in self.notes():
            if slugify(note.title) == target:
                return note.path
        return None

    def get(self, title: str) -> Note | None:
        path = self.resolve(title)
        return Note.read(path) if path else None

    def exists(self, title: str) -> bool:
        return self.resolve(title) is not None

    # -- writing ------------------------------------------------------------
    def write(self, note: Note, kind: str | None = None) -> Path:
        kind = kind or note.kind
        path = note.path or self.path_for(note.title, kind)
        note.meta.setdefault("type", kind)
        return note.write(path)

    def upsert(
        self,
        title: str,
        kind: str,
        body: str = "",
        meta: dict | None = None,
        *,
        overwrite_body: bool = False,
    ) -> Note:
        """Create the note, or merge into it if a human has been editing it.

        Merging matters: the GM is expected to hand-edit NPCs between
        sessions, and the engine must not stomp on that.
        """
        existing = self.get(title)
        if existing is None:
            note = Note(title=title, body=body, meta={"type": kind, **(meta or {})})
            self.write(note, kind)
            return note
        if meta:
            existing.meta.update(meta)
        if overwrite_body and body:
            existing.body = body
        existing.meta.setdefault("type", kind)
        existing.write()
        return existing

    def write_many(self, notes: Iterable[Note], kind: str | None = None) -> list[Path]:
        return [self.write(n, kind) for n in notes]
