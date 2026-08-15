"""corpus/ -> vault notes -> searchable index.

Run `srgm ingest` once after dropping files in `corpus/`, then `srgm reindex`
whenever you have hand-edited the vault. Ingest is idempotent: re-running it
replaces the source notes it owns and leaves everything else alone.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..retrieval.embed import get_embedder
from ..retrieval.store import Chunk, ChunkStore
from ..vaultio import Note, Vault
from .chunker import chunk_sections
from .extract import extract_corpus
from .linker import EntityLinker

Progress = Callable[[str], None]


@dataclass
class IngestReport:
    files_seen: int = 0
    files_ingested: int = 0
    notes_written: int = 0
    stubs_created: int = 0
    chunks_indexed: int = 0
    entities_found: int = 0
    proposals: list[tuple[str, int]] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)
    seconds: float = 0.0

    def summary(self) -> str:
        lines = [
            f"  files seen      {self.files_seen}",
            f"  files ingested  {self.files_ingested}",
            f"  notes written   {self.notes_written}",
            f"  stub notes      {self.stubs_created}",
            f"  chunks indexed  {self.chunks_indexed}",
            f"  entities linked {self.entities_found}",
            f"  elapsed         {self.seconds:.1f}s",
        ]
        if self.errors:
            lines.append(f"  errors          {len(self.errors)}")
            lines.extend(f"    ! {name}: {msg}" for name, msg in self.errors[:5])
        return "\n".join(lines)


def _source_note_body(doc_title: str, sections, linker: EntityLinker) -> tuple[str, list[str]]:
    """Render an extracted document as a single linked markdown note."""
    parts: list[str] = []
    all_entities: list[str] = []
    last_heading = None
    for section in sections:
        if section.heading and section.heading != last_heading:
            parts.append(f"## {section.heading}")
            last_heading = section.heading
        linked, entities = linker.link(section.text)
        all_entities.extend(entities)
        parts.append(linked)
    return "\n\n".join(parts), all_entities


def ingest_corpus(
    corpus_dir: Path,
    vault_dir: Path,
    index_path: Path,
    *,
    embed_backend: str = "auto",
    embed_model: str | None = None,
    create_stubs: bool = True,
    progress: Progress | None = None,
) -> IngestReport:
    """Extract every supported file under `corpus_dir` into the vault."""
    started = time.time()
    say = progress or (lambda _msg: None)
    report = IngestReport()

    vault = Vault(vault_dir)
    vault.ensure()

    project_lexicon = Path(vault_dir).parent / "lexicon.json"
    linker = EntityLinker(lexicon=_merged_lexicon(project_lexicon))

    corpus_dir = Path(corpus_dir)
    if not corpus_dir.is_dir():
        report.errors.append((str(corpus_dir), "corpus directory does not exist"))
        report.seconds = time.time() - started
        return report

    seen_entities: dict[str, str] = {}
    corpus_text_sample: list[str] = []

    for path, doc, error in extract_corpus(corpus_dir):
        report.files_seen += 1
        if error:
            report.errors.append((path.name, error))
            say(f"  ! {path.name}: {error}")
            continue
        if doc is None:
            say(f"  - {path.name}: no extractable text")
            continue

        say(f"  + {path.name} ({doc.word_count():,} words, {len(doc.sections)} sections)")
        body, entities = _source_note_body(doc.title, doc.sections, linker)

        rel = path.relative_to(corpus_dir).as_posix()
        note = Note(
            title=doc.title,
            body=body,
            meta={
                "type": "source",
                "source_file": rel,
                "format": doc.meta.get("format", ""),
                "sections": len(doc.sections),
                "words": doc.word_count(),
                "ingested": time.strftime("%Y-%m-%d"),
                "tags": ["source", "corpus"],
            },
        )
        vault.write(note, "source")
        report.files_ingested += 1
        report.notes_written += 1

        for name, kind in linker.find(doc.text[:200_000]):
            seen_entities.setdefault(name, kind)
        report.entities_found += len(set(entities))
        corpus_text_sample.append(doc.text[:120_000])

    # Stub notes for entities the corpus mentions but the vault lacks.
    if create_stubs and seen_entities:
        for title, kind, body in linker.stub_notes(sorted(seen_entities.items())):
            if vault.exists(title):
                continue
            vault.upsert(
                title,
                kind,
                body=body,
                meta={"tags": ["auto", "entity"], "entity_kind": seen_entities[title]},
            )
            report.stubs_created += 1

    # Names your books use that the lexicon has never heard of.
    if corpus_text_sample:
        report.proposals = linker.propose_entities("\n".join(corpus_text_sample))
        if report.proposals:
            _write_proposal_note(vault, report.proposals)

    say("Indexing vault...")
    report.chunks_indexed = reindex_vault(
        vault_dir,
        index_path,
        embed_backend=embed_backend,
        embed_model=embed_model,
        progress=say,
    )
    report.seconds = time.time() - started
    return report


def _merged_lexicon(project_lexicon: Path) -> dict[str, list[str]]:
    from .linker import load_lexicon

    return load_lexicon(project_lexicon if project_lexicon.is_file() else None)


def _write_proposal_note(vault: Vault, proposals: list[tuple[str, int]]) -> None:
    rows = "\n".join(f"| {name} | {count} |" for name, count in proposals)
    body = (
        "Names that show up repeatedly in your corpus but are not in the "
        "lexicon yet. Add the good ones to `lexicon.json` in the project root "
        "and re-run `srgm ingest` -- they will be linked everywhere from then "
        "on.\n\n"
        "| Candidate | Mentions |\n|---|---|\n" + rows + "\n"
    )
    vault.upsert(
        "Entity Proposals",
        "meta",
        body=body,
        meta={"tags": ["meta", "review"]},
        overwrite_body=True,
    )


_PLACEHOLDER = re.compile(r"^[_*\s-]*(none|none yet|quiet|todo|unstated|unknown|uncast)?[_*\s-]*$", re.IGNORECASE)


def _worth_indexing(text: str) -> bool:
    """Filter out empty section stubs.

    Short placeholder sections ("_none yet_") score absurdly well on BM25 --
    a two-word document matches everything -- and crowd out real passages.
    """
    stripped = text.strip()
    if _PLACEHOLDER.match(stripped):
        return False
    return len(stripped.split()) >= 5 and len(stripped) >= 24


def reindex_vault(
    vault_dir: Path,
    index_path: Path,
    *,
    embed_backend: str = "auto",
    embed_model: str | None = None,
    progress: Progress | None = None,
) -> int:
    """Rebuild the chunk index from the current state of the vault."""
    say = progress or (lambda _msg: None)
    vault = Vault(vault_dir)
    store = ChunkStore(Path(index_path))
    store.clear()

    embedder = get_embedder(embed_backend, embed_model)
    say(f"  embedder: {embedder.name} (dim {embedder.dim})")

    from ..ingest.extract import split_into_sections  # local import avoids a cycle

    pending: list[Chunk] = []
    for note in vault.notes():
        if note.kind == "template":
            continue
        sections = split_into_sections(note.body, default_heading="")
        for piece in chunk_sections(sections):
            if not _worth_indexing(piece.text):
                continue
            pending.append(
                Chunk(
                    text=piece.text,
                    note=note.title,
                    kind=note.kind,
                    source=str(note.meta.get("source_file", "")),
                    heading=piece.heading,
                )
            )

    if not pending:
        store.set_meta("embedder", embedder.name)
        store.close()
        return 0

    batch = 128
    for start in range(0, len(pending), batch):
        window = pending[start : start + batch]
        # Embed the note and heading alongside the body, matching what BM25
        # indexes, so both signals agree on what a chunk is about.
        vectors = embedder.encode([c.index_text for c in window])
        for chunk, vector in zip(window, vectors):
            chunk.vector = vector
        say(f"  embedded {min(start + batch, len(pending))}/{len(pending)} chunks")

    store.add_many(pending)
    store.build_index()
    store.set_meta("embedder", embedder.name)
    store.set_meta("built", time.strftime("%Y-%m-%dT%H:%M:%S"))
    total = store.count()
    store.close()
    return total
