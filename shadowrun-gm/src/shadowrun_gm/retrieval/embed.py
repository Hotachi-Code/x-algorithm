"""Embeddings, with a graceful ladder of backends.

sentence-transformers if it is installed (best quality, still fully local),
otherwise a hashed bag-of-bigrams vector. The hashing backend is not as sharp,
but it is deterministic, dependency-free, and good enough that hybrid search
still beats pure keyword matching -- which means the project works the moment
it is cloned.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field
from typing import Protocol, Sequence

TOKEN_RE = re.compile(r"[a-z0-9']+")


def tokenize(text: str) -> list[str]:
    return TOKEN_RE.findall(text.lower())


class Embedder(Protocol):
    name: str
    dim: int

    def encode(self, texts: Sequence[str]) -> list[list[float]]: ...


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


@dataclass
class HashingEmbedder:
    """Hashed character-bigram + token vector, L2 normalised.

    Tokens and bigrams both feed the vector so that "Renraku" and "Renraku's"
    land near each other without a stemmer.
    """

    dim: int = 512
    name: str = field(default="hashing", init=False)

    def _bucket(self, feature: str) -> int:
        digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
        return int.from_bytes(digest, "big") % self.dim

    def encode_one(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        tokens = tokenize(text)
        for token in tokens:
            vec[self._bucket(token)] += 1.0
        for a, b in zip(tokens, tokens[1:]):
            vec[self._bucket(f"{a}_{b}")] += 0.5
        # Sub-linear scaling keeps a long note from drowning a short one.
        vec = [math.log1p(v) for v in vec]
        norm = math.sqrt(sum(v * v for v in vec))
        if norm:
            vec = [v / norm for v in vec]
        return vec

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        return [self.encode_one(t) for t in texts]


@dataclass
class SentenceTransformerEmbedder:
    model_name: str = "sentence-transformers/all-MiniLM-L6-v2"
    name: str = field(default="sentence-transformers", init=False)
    dim: int = field(default=384, init=False)

    def __post_init__(self) -> None:
        from sentence_transformers import SentenceTransformer  # noqa: PLC0415

        self._model = SentenceTransformer(self.model_name)
        self.dim = int(self._model.get_sentence_embedding_dimension())

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        vectors = self._model.encode(
            list(texts), normalize_embeddings=True, show_progress_bar=False
        )
        return [list(map(float, v)) for v in vectors]


def get_embedder(backend: str = "auto", model: str | None = None) -> Embedder:
    """Pick an embedding backend, degrading quietly rather than failing."""
    if backend in ("none", "off"):
        return HashingEmbedder(dim=64)
    if backend in ("auto", "sentence-transformers", "st"):
        try:
            return SentenceTransformerEmbedder(
                model or "sentence-transformers/all-MiniLM-L6-v2"
            )
        except Exception:
            if backend != "auto":
                raise
    return HashingEmbedder()
