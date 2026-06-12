import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


TOKEN_RE = re.compile(r"[\wа-яА-ЯёЁ#.]+", re.UNICODE)


def tokenize(text: str) -> set[str]:
    return {token.lower() for token in TOKEN_RE.findall(text) if len(token) > 2}


@dataclass(frozen=True)
class Chunk:
    id: str
    text: str
    source_path: str
    source_name: str
    chunk_index: int
    metadata: dict[str, Any]


class LocalJsonlRetriever:
    def __init__(self, chunks_path: Path):
        self.chunks_path = chunks_path
        self._chunks: list[Chunk] | None = None

    def _load(self) -> list[Chunk]:
        if self._chunks is not None:
            return self._chunks

        chunks: list[Chunk] = []
        if not self.chunks_path.exists():
            self._chunks = chunks
            return chunks

        with self.chunks_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                raw = json.loads(line)
                metadata = raw.get("metadata") or {}
                if metadata.get("verification_status") in {"blocked", "deprecated"}:
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

    def search(self, query: str, top_k: int) -> list[Chunk]:
        query_tokens = tokenize(query)
        if not query_tokens:
            return []

        scored: list[tuple[float, Chunk]] = []
        for chunk in self._load():
            chunk_tokens = tokenize(chunk.text)
            overlap = query_tokens.intersection(chunk_tokens)
            if not overlap:
                continue
            score = len(overlap) / max(len(query_tokens), 1)
            scored.append((score, chunk))

        scored.sort(key=lambda item: item[0], reverse=True)
        return [chunk for _, chunk in scored[:top_k]]

