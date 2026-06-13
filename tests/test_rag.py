import json
from pathlib import Path

from tekla_agent.rag import LocalJsonlRetriever


def _write_corpus(path: Path, records: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _chunk(cid: str, text: str, **meta) -> dict:
    return {
        "id": cid,
        "text": text,
        "source_path": f"/corpus/{cid}.md",
        "source_name": cid,
        "chunk_index": 0,
        "metadata": meta,
    }


def test_bm25_ranks_relevant_chunk_first(tmp_path: Path) -> None:
    corpus = tmp_path / "chunks.jsonl"
    _write_corpus(
        corpus,
        [
            _chunk("beam", "Создание балки CreateBeam профиль HEA300 материал S355 в Tekla."),
            _chunk("rebar", "Армирование плиты, расчет защитного слоя бетона и шага стержней."),
            _chunk("noise", "Общая документация по проекту и порядок согласования чертежей."),
        ],
    )
    retriever = LocalJsonlRetriever(corpus)
    results = retriever.search("как создать балку HEA300", top_k=3)
    assert results
    assert results[0].chunk.id == "beam"
    assert results[0].score > 0


def test_blocked_chunks_excluded(tmp_path: Path) -> None:
    corpus = tmp_path / "chunks.jsonl"
    _write_corpus(
        corpus,
        [
            _chunk("ok", "балка профиль HEA300", verification_status="verified"),
            _chunk("bad", "балка профиль HEA300", verification_status="blocked"),
        ],
    )
    retriever = LocalJsonlRetriever(corpus)
    results = retriever.search("балка HEA300", top_k=5)
    ids = {r.chunk.id for r in results}
    assert "ok" in ids
    assert "bad" not in ids


def test_metadata_filter(tmp_path: Path) -> None:
    corpus = tmp_path / "chunks.jsonl"
    _write_corpus(
        corpus,
        [
            _chunk("v2021", "балка HEA300", tekla_version="2021"),
            _chunk("v2023", "балка HEA300", tekla_version="2023"),
        ],
    )
    retriever = LocalJsonlRetriever(corpus)
    results = retriever.search("балка", top_k=5, metadata_filter={"tekla_version": "2023"})
    ids = {r.chunk.id for r in results}
    assert ids == {"v2023"}


def test_empty_query_returns_nothing(tmp_path: Path) -> None:
    corpus = tmp_path / "chunks.jsonl"
    _write_corpus(corpus, [_chunk("a", "балка HEA300")])
    retriever = LocalJsonlRetriever(corpus)
    assert retriever.search("!!", top_k=5) == []
