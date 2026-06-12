# RAG Corpus Specification

## Required source groups

- Tekla Open API docs and official examples.
- Internal C# examples that already work with Tekla.
- RD templates and release rules.
- Typical structural nodes and reinforcement examples.
- User corrections from pilot sessions.
- Known errors and fixed prompts/tool calls.

## Chunk format

`scripts/chunk_corpus.py` writes JSONL records:

```json
{
  "id": "sha256:...",
  "text": "chunk text",
  "source_path": "docs/example.md",
  "source_name": "docs",
  "chunk_index": 0,
  "char_start": 0,
  "char_end": 1800,
  "metadata": {
    "verification_status": "unverified",
    "tekla_version": "unknown"
  }
}
```

## Verification statuses

- `raw`: imported but not reviewed.
- `unverified`: readable but not checked by a Tekla engineer.
- `verified`: checked and accepted for RAG.
- `deprecated`: retained for traceability but excluded from retrieval.
- `blocked`: unsafe or legally unsuitable.

## Dataset for later fine-tuning

Do not train directly on raw docs. Build SFT rows only after review:

```json
{
  "instruction": "Create a HEA300 beam...",
  "context": "Relevant Tekla API context...",
  "expected_tool_calls": [{"tool": "CreateBeam", "args": {}}],
  "expected_code": "optional C# after promotion",
  "tests": ["compiles", "dry_run_success"],
  "reviewer": "engineer",
  "status": "verified"
}
```

