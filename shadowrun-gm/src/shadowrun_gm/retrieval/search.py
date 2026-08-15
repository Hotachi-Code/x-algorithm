"""The retriever that assembles the Director's context window.

Three signals are blended:

  lexical  BM25 over chunk text -- catches proper nouns and rules keywords
  vector   cosine over embeddings -- catches paraphrase
  graph    notes adjacent in the vault to whatever already scored well

The graph term is the part that makes an Obsidian vault worth having. A hit on
"Hollow" pulls in the fixer note that links to her even if that note never
uses the word.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..vaultio import Vault, VaultGraph
from ..vaultio.note import slugify
from .embed import Embedder, cosine, get_embedder
from .store import Chunk, ChunkStore, normalise_scores


@dataclass
class SearchHit:
    chunk: Chunk
    score: float
    lexical: float = 0.0
    vector: float = 0.0
    graph: float = 0.0
    boost: float = 1.0

    def cite(self) -> str:
        src = f" ({self.chunk.source})" if self.chunk.source else ""
        return f"[[{self.chunk.note}]]{src}"


# Notes about the live campaign matter more than a rulebook paragraph when
# both match. Weighting by note type is cheap and noticeably improves scenes.
KIND_BOOST = {
    "thread": 1.45,
    "npc": 1.35,
    "runner": 1.35,
    "scene": 1.25,
    "session": 1.2,
    "location": 1.2,
    "faction": 1.15,
    "campaign": 1.1,
    "setting": 1.0,
    "source": 0.9,
    "system": 0.85,
}


@dataclass
class Retriever:
    vault: Vault
    store: ChunkStore
    embedder: Embedder | None = None
    graph: VaultGraph | None = None
    w_lexical: float = 1.0
    w_vector: float = 0.85
    w_graph: float = 0.45
    graph_hops: int = 1
    _query_cache: dict[str, list[float]] = field(default_factory=dict, repr=False)

    @classmethod
    def open(
        cls,
        vault_dir: Path,
        index_path: Path,
        *,
        embed_backend: str = "auto",
        embed_model: str | None = None,
        graph_hops: int = 1,
    ) -> "Retriever":
        vault = Vault(vault_dir)
        return cls(
            vault=vault,
            store=ChunkStore(index_path),
            embedder=get_embedder(embed_backend, embed_model),
            graph=VaultGraph.build(vault),
            graph_hops=graph_hops,
        )

    # -- search -------------------------------------------------------------
    def search(
        self,
        query: str,
        k: int = 12,
        *,
        kinds: set[str] | None = None,
        pin_notes: list[str] | None = None,
    ) -> list[SearchHit]:
        """Rank chunks for a query, optionally pinning notes the scene needs."""
        chunks = self.store.all_chunks()
        if not chunks:
            return []
        by_id = {c.id: c for c in chunks if c.id is not None}

        lexical = normalise_scores(self.store.bm25(query, limit=max(k * 8, 60)))

        vector: dict[int, float] = {}
        if self.embedder is not None:
            qvec = self._embed_query(query)
            raw = [
                (c.id, cosine(qvec, c.vector))
                for c in chunks
                if c.id is not None and c.vector
            ]
            raw = [(i, s) for i, s in raw if s > 0]
            raw.sort(key=lambda kv: -kv[1])
            vector = normalise_scores(raw[: max(k * 8, 60)])

        graph_scores = self._graph_scores(lexical, vector, by_id, pin_notes)

        combined: dict[int, SearchHit] = {}
        for cid in set(lexical) | set(vector) | set(graph_scores):
            chunk = by_id.get(cid)
            if chunk is None:
                continue
            if kinds and chunk.kind not in kinds:
                continue
            lex = lexical.get(cid, 0.0)
            vec = vector.get(cid, 0.0)
            grf = graph_scores.get(cid, 0.0)
            boost = KIND_BOOST.get(chunk.kind, 1.0)
            score = (
                self.w_lexical * lex + self.w_vector * vec + self.w_graph * grf
            ) * boost
            combined[cid] = SearchHit(
                chunk=chunk, score=score, lexical=lex, vector=vec, graph=grf, boost=boost
            )

        ranked = sorted(combined.values(), key=lambda h: -h.score)
        return self._diversify(ranked, k)

    def _embed_query(self, query: str) -> list[float]:
        if query not in self._query_cache:
            assert self.embedder is not None
            self._query_cache[query] = self.embedder.encode([query])[0]
        return self._query_cache[query]

    def _graph_scores(
        self,
        lexical: dict[int, float],
        vector: dict[int, float],
        by_id: dict[int, Chunk],
        pin_notes: list[str] | None,
    ) -> dict[int, float]:
        """Spread credit from strong hits to their linked neighbours."""
        if self.graph is None:
            return {}

        seed_scores: dict[str, float] = {}
        for cid, score in list(lexical.items()) + list(vector.items()):
            chunk = by_id.get(cid)
            if chunk is None:
                continue
            slug = slugify(chunk.note)
            seed_scores[slug] = max(seed_scores.get(slug, 0.0), score)
        for title in pin_notes or []:
            seed_scores[slugify(title)] = 1.0

        # Only the strongest seeds get to pull in neighbours, or everything
        # ends up adjacent to everything.
        top_seeds = sorted(seed_scores.items(), key=lambda kv: -kv[1])[:8]

        note_bonus: dict[str, float] = {}
        for slug, score in top_seeds:
            note_bonus[slug] = max(note_bonus.get(slug, 0.0), score)
            for hop in range(1, self.graph_hops + 1):
                decay = 0.6 ** hop
                for neighbour in self.graph.neighbours(slug, hops=hop):
                    nslug = slugify(neighbour.title)
                    note_bonus[nslug] = max(note_bonus.get(nslug, 0.0), score * decay)

        out: dict[int, float] = {}
        for cid, chunk in by_id.items():
            bonus = note_bonus.get(slugify(chunk.note))
            if bonus:
                out[cid] = bonus
        return out

    @staticmethod
    def _diversify(ranked: list[SearchHit], k: int, per_note: int = 3) -> list[SearchHit]:
        """Cap chunks per note so one long rulebook page cannot fill the window."""
        counts: dict[str, int] = {}
        out: list[SearchHit] = []
        overflow: list[SearchHit] = []
        for hit in ranked:
            note = hit.chunk.note
            if counts.get(note, 0) < per_note:
                counts[note] = counts.get(note, 0) + 1
                out.append(hit)
            else:
                overflow.append(hit)
            if len(out) >= k:
                return out
        return (out + overflow)[:k]

    # -- context assembly ---------------------------------------------------
    def context_block(
        self,
        query: str,
        k: int = 12,
        *,
        char_budget: int = 6000,
        pin_notes: list[str] | None = None,
        kinds: set[str] | None = None,
    ) -> tuple[str, list[SearchHit]]:
        """Render retrieved chunks as a citable block for the prompt."""
        hits = self.search(query, k=k, pin_notes=pin_notes, kinds=kinds)
        parts: list[str] = []
        used = 0
        kept: list[SearchHit] = []
        for hit in hits:
            text = hit.chunk.text.strip()
            entry = f"### {hit.chunk.label()}\n{text}"
            if used + len(entry) > char_budget and kept:
                break
            parts.append(entry)
            kept.append(hit)
            used += len(entry)
        return "\n\n".join(parts), kept
