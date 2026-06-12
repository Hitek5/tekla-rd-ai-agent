# Data directories

This folder is intentionally mostly ignored by git.

Expected local layout:

```text
data/
  raw/
    internal-tekla-examples/
    rd-templates/
    audit-lessons/
  rag/
    chunks.jsonl
    qdrant/
  models/
    ollama/
    gguf/
    hf/
  checkpoints/
  audit/
  eval/
    runs/
```

Do not commit private company models, RD documents, project models, or audit logs.

