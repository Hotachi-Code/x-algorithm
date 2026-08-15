"""SQLite-backed chunk store with a BM25 index built at load time.

Chunks carry their source note title so every retrieved passage can be handed
back to the graph for neighbourhood expansion, and so the Director can cite
where a rule or a piece of lore came from.
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

from .embed import tokenize

SCHEMA = """
CREATE TABLE IF NOT EXISTS chunks (
    id        INTEGER PRIMARY KEY,
    note      TEXT NOT NULL,
    kind      TEXT NOT NULL DEFAULT 'note',
    source    TEXT NOT NULL DEFAULT '',
    heading   TEXT NOT NULL DEFAULT '',
    text      TEXT NOT NULL,
    vector    TEXT,
    checksum  TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS chunks_note ON chunks(note);
CREATE INDEX IF NOT EXISTS chunks_kind ON chunks(kind);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


@dataclass
class Chunk:
    text: str
    note: str
    kind: str = "note"
    source: str = ""
    heading: str = ""
    id: int | None = None
    vector: list[float] | None = None

    def label(self) -> str:
        return f"{self.note}#{self.heading}" if self.heading else self.note

    @property
    def index_text(self) -> str:
        """What gets searched: the body plus the note and section it lives in.

        Without this a note is unfindable by its own name -- an NPC note whose
        body never repeats the NPC's name would never match a search for them,
        which is exactly the common case in a vault.
        """
        return "\n".join(part for part in (self.note, self.heading, self.text) if part)


@dataclass
class ChunkStore:
    """Persisted chunks plus an in-memory BM25 index."""

    path: Path
    k1: float = 1.5
    b: float = 0.75

    _conn: sqlite3.Connection | None = field(default=None, init=False, repr=False)
    _df: Counter = field(default_factory=Counter, init=False, repr=False)
    _postings: dict[str, list[tuple[int, int]]] = field(
        default_factory=lambda: defaultdict(list), init=False, repr=False
    )
    _lengths: dict[int, int] = field(default_factory=dict, init=False, repr=False)
    _avg_len: float = field(default=0.0, init=False, repr=False)
    _indexed: bool = field(default=False, init=False, repr=False)

    # -- lifecycle ----------------------------------------------------------
    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(self.path)
            self._conn.row_factory = sqlite3.Row
            self._conn.executescript(SCHEMA)
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # -- writing ------------------------------------------------------------
    def clear(self) -> None:
        self.conn.execute("DELETE FROM chunks")
        self.conn.commit()
        self._indexed = False

    def delete_note(self, note: str) -> None:
        self.conn.execute("DELETE FROM chunks WHERE note = ?", (note,))
        self.conn.commit()
        self._indexed = False

    def add_many(self, chunks: Iterable[Chunk]) -> int:
        rows = [
            (
                c.note,
                c.kind,
                c.source,
                c.heading,
                c.text,
                json.dumps(c.vector) if c.vector is not None else None,
                _checksum(c.text),
            )
            for c in chunks
        ]
        if not rows:
            return 0
        self.conn.executemany(
            "INSERT INTO chunks (note, kind, source, heading, text, vector, checksum)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        self.conn.commit()
        self._indexed = False
        return len(rows)

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self.conn.commit()

    def get_meta(self, key: str, default: str = "") -> str:
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    # -- reading ------------------------------------------------------------
    def count(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"])

    def all_chunks(self) -> list[Chunk]:
        rows = self.conn.execute(
            "SELECT id, note, kind, source, heading, text, vector FROM chunks"
        ).fetchall()
        return [_row_to_chunk(r) for r in rows]

    def get(self, chunk_id: int) -> Chunk | None:
        row = self.conn.execute(
            "SELECT id, note, kind, source, heading, text, vector FROM chunks WHERE id = ?",
            (chunk_id,),
        ).fetchone()
        return _row_to_chunk(row) if row else None

    def notes(self) -> list[str]:
        rows = self.conn.execute("SELECT DISTINCT note FROM chunks ORDER BY note").fetchall()
        return [r["note"] for r in rows]

    # -- lexical index ------------------------------------------------------
    def build_index(self) -> None:
        self._df.clear()
        self._postings = defaultdict(list)
        self._lengths.clear()
        rows = self.conn.execute(
            "SELECT id, note, heading, text FROM chunks"
        ).fetchall()
        for row in rows:
            tokens = tokenize(
                "\n".join(
                    part for part in (row["note"], row["heading"], row["text"]) if part
                )
            )
            self._lengths[row["id"]] = len(tokens)
            counts = Counter(tokens)
            for term, tf in counts.items():
                self._postings[term].append((row["id"], tf))
                self._df[term] += 1
        total = sum(self._lengths.values())
        self._avg_len = (total / len(self._lengths)) if self._lengths else 0.0
        self._indexed = True

    def bm25(self, query: str, limit: int = 50) -> list[tuple[int, float]]:
        """Okapi BM25 over the chunk texts."""
        if not self._indexed:
            self.build_index()
        n_docs = len(self._lengths)
        if not n_docs:
            return []
        scores: dict[int, float] = defaultdict(float)
        for term in set(tokenize(query)):
            postings = self._postings.get(term)
            if not postings:
                continue
            df = self._df[term]
            idf = math.log(1 + (n_docs - df + 0.5) / (df + 0.5))
            for doc_id, tf in postings:
                length = self._lengths[doc_id] or 1
                denom = tf + self.k1 * (1 - self.b + self.b * length / (self._avg_len or 1))
                scores[doc_id] += idf * (tf * (self.k1 + 1)) / (denom or 1)
        ranked = sorted(scores.items(), key=lambda kv: -kv[1])
        return ranked[:limit]


def _checksum(text: str) -> str:
    import hashlib

    return hashlib.blake2b(text.encode("utf-8"), digest_size=8).hexdigest()


def _row_to_chunk(row: sqlite3.Row) -> Chunk:
    vector: list[float] | None = None
    if row["vector"]:
        try:
            vector = json.loads(row["vector"])
        except json.JSONDecodeError:
            vector = None
    return Chunk(
        id=row["id"],
        note=row["note"],
        kind=row["kind"],
        source=row["source"],
        heading=row["heading"],
        text=row["text"],
        vector=vector,
    )


def normalise_scores(pairs: Sequence[tuple[int, float]]) -> dict[int, float]:
    """Min-max to [0,1] so lexical and vector scores can be blended."""
    if not pairs:
        return {}
    values = [v for _, v in pairs]
    lo, hi = min(values), max(values)
    span = hi - lo
    if span <= 0:
        return {k: 1.0 for k, _ in pairs}
    return {k: (v - lo) / span for k, v in pairs}
