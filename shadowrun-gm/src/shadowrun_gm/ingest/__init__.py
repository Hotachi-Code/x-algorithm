"""Corpus ingestion: your books and notes become linked vault notes.

Nothing copyrighted ships with this project. You point `corpus/` at material
you legally own or that is freely distributed, and the pipeline converts it
into markdown the engine can search. See `docs/CORPUS.md`.
"""

from .extract import ExtractedDoc, Section, extract_file, extract_corpus
from .chunker import chunk_sections, chunk_text
from .linker import EntityLinker, load_lexicon
from .pipeline import IngestReport, ingest_corpus, reindex_vault

__all__ = [
    "ExtractedDoc",
    "Section",
    "extract_file",
    "extract_corpus",
    "chunk_text",
    "chunk_sections",
    "EntityLinker",
    "load_lexicon",
    "ingest_corpus",
    "reindex_vault",
    "IngestReport",
]
