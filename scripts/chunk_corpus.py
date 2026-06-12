#!/usr/bin/env python
"""Create a local JSONL RAG corpus from text-like files."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


TEXT_EXTENSIONS = {
    ".cs",
    ".json",
    ".md",
    ".ps1",
    ".py",
    ".txt",
    ".yaml",
    ".yml",
}


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def chunk_text(text: str, target_chars: int, overlap_chars: int) -> list[tuple[int, int, str]]:
    chunks: list[tuple[int, int, str]] = []
    start = 0
    text_length = len(text)
    while start < text_length:
        end = min(start + target_chars, text_length)
        chunk = text[start:end].strip()
        if chunk:
            chunks.append((start, end, chunk))
        if end == text_length:
            break
        start = max(0, end - overlap_chars)
    return chunks


def record_id(path: Path, chunk_index: int, text: str) -> str:
    digest = hashlib.sha256(f"{path}:{chunk_index}:{text}".encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def iter_files(source: Path) -> list[Path]:
    if source.is_file():
        return [source]
    return sorted(path for path in source.rglob("*") if path.is_file() and path.suffix in TEXT_EXTENSIONS)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--target-chars", default=1800, type=int)
    parser.add_argument("--overlap-chars", default=250, type=int)
    parser.add_argument("--source-name", default=None)
    parser.add_argument("--tekla-version", default="unknown")
    parser.add_argument("--verification-status", default="unverified")
    args = parser.parse_args()

    source = args.source
    output = args.output
    output.parent.mkdir(parents=True, exist_ok=True)

    count = 0
    with output.open("w", encoding="utf-8") as handle:
        for path in iter_files(source):
            text = read_text(path)
            source_name = args.source_name or source.name or "corpus"
            for chunk_index, (start, end, chunk) in enumerate(
                chunk_text(text, args.target_chars, args.overlap_chars)
            ):
                record = {
                    "id": record_id(path, chunk_index, chunk),
                    "text": chunk,
                    "source_path": str(path),
                    "source_name": source_name,
                    "chunk_index": chunk_index,
                    "char_start": start,
                    "char_end": end,
                    "metadata": {
                        "verification_status": args.verification_status,
                        "tekla_version": args.tekla_version,
                    },
                }
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
                count += 1

    print(f"Wrote {count} chunks to {output}")


if __name__ == "__main__":
    main()

