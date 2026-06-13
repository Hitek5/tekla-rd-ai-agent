"""Local retrieval over the JSONL corpus.

The MVP scored chunks by Jaccard overlap of unique tokens: a chunk that mentions
a query word once scored the same as one that is *about* that word, long chunks
were penalised arbitrarily, and common words counted as much as rare ones. Recall
was poor — bad for a RAG system whose whole job is finding the right C# example
or RD rule.

This replaces it with **BM25 Okapi**, the standard lexical ranking function:
term-frequency saturation, inverse document frequency (rare terms matter more),
and length normalisation. It is implemented in pure Python on purpose — no extra
wheel to mirror into the air-gapped host, no GPU, fast enough for the tens of
thousands of chunks a pilot corpus contains.

The index is built once and cached. ``RetrievedChunk`` carries the score so the
chat layer can cite sources with confidence, and ``search`` supports metadata
filtering (e.g. restrict to a Tekla version or to ``verified`` chunks only).
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

TOKEN_RE = re.compile(r"[\wа-яА-ЯёЁ#.]+", re.UNICODE)

# Chunks in these states are never returned.
_EXCLUDED_STATUSES = {"blocked", "deprecated"}

# BM25 free parameters (standard defaults).
_K1 = 1.5
_B = 0.75


def tokenize(text: str) -> list[str]:
    """Tokenise to a *list* (BM25 needs term frequencies, not a set)."""
    return [token.lower() for token in TOKEN_RE.findall(text) if len(token) > 2]


@dataclass(frozen=True)
class Chunk:
    id: str
    text: str
    source_path: str
    source_name: str
    chunk_index: int
    metadata: dict[str, Any]


@dataclass(frozen=True)
class RetrievedChunk:
    chunk: Chunk
    score: float


@dataclass
class _Index:
    tokens: list[list[str]] = field(default_factory=list)
    doc_freq: Counter[str] = field(default_factory=Counter)
    doc_len: list[int] = field(default_factory=list)
    avg_len: float = 0.0
    n_docs: int = 0


class LocalJsonlRetriever:
    def __init__(self, chunks_path: Path):
        self.chunks_path = chunks_path
        self._chunks: list[Chunk] | None = None
        self._index: _Index | None = None

    # -- loading -----------------------------------------------------------

    def _load(self) -> list[Chunk]:
        if self._chunks is not None:
            return self._chunks

        chunks: list[Chunk] = []
        if self.chunks_path.exists():
            with self.chunks_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    raw = json.loads(line)
                    metadata = raw.get("metadata") or {}
                    if metadata.get("verification_status") in _EXCLUDED_STATUSES:
                        continue
                    chunks.append(
                        Chunk(
                            id=str(raw["id"]),
                            text=str(raw["text"]),
                            source_path=str(raw.get("source_path", "")),
                            source_name=str(raw.get("source_name", "")),
                            chunk_index=int(raw.get("chunk_index", 0)),
                            metadata=metadata,
                        )
                    )
        self._chunks = chunks
        return chunks

    def _build_index(self) -> _Index:
        if self._index is not None:
            return self._index

        index = _Index()
        for chunk in self._load():
            tokens = tokenize(chunk.text)
            index.tokens.append(tokens)
            index.doc_len.append(len(tokens))
            for term in set(tokens):
                index.doc_freq[term] += 1

        index.n_docs = len(index.tokens)
        index.avg_len = (sum(index.doc_len) / index.n_docs) if index.n_docs else 0.0
        self._index = index
        return index

    # -- scoring -----------------------------------------------------------

    def _bm25_score(self, query_terms: list[str], doc_i: int, index: _Index) -> float:
        if index.avg_len == 0:
            return 0.0
        tf = Counter(index.tokens[doc_i])
        dl = index.doc_len[doc_i]
        score = 0.0
        for term in query_terms:
            f = tf.get(term, 0)
            if f == 0:
                continue
            n_qi = index.doc_freq.get(term, 0)
            # idf with +0.5 smoothing; max(…, eps) avoids negative idf for
            # terms present in more than half the corpus.
            idf = math.log(1 + (index.n_docs - n_qi + 0.5) / (n_qi + 0.5))
            denom = f + _K1 * (1 - _B + _B * dl / index.avg_len)
            score += idf * (f * (_K1 + 1)) / denom
        return score

    def search(
        self,
        query: str,
        top_k: int,
        *,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[RetrievedChunk]:
        query_terms = tokenize(query)
        if not query_terms:
            return []

        chunks = self._load()
        index = self._build_index()

        scored: list[RetrievedChunk] = []
        for i, chunk in enumerate(chunks):
            if metadata_filter and not _matches(chunk.metadata, metadata_filter):
                continue
            score = self._bm25_score(query_terms, i, index)
            if score > 0:
                scored.append(RetrievedChunk(chunk=chunk, score=score))

        scored.sort(key=lambda item: item.score, reverse=True)
        return scored[:top_k]


def _matches(metadata: dict[str, Any], wanted: dict[str, Any]) -> bool:
    for key, value in wanted.items():
        actual = metadata.get(key)
        if isinstance(value, (list, tuple, set)):
            if actual not in value:
                return False
        elif actual != value:
            return False
    return True
