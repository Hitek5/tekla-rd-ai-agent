# Changelog

## 0.2.0 — Pilot-hardening for the closed loop (КСПД)

Turns the 0.1 scaffold into a security-reviewable, VRAM-budgeted, actually-agentic
MVP. Focus areas: closed-loop deployment readiness, security best practices
(RU + international), and working functionality on 12–24 GB GPUs.

### Added — agent loop & functionality
- `POST /agent/plan`: the model proposes a single typed tool call; the orchestrator
  extracts it, validates arguments, and runs policy in dry-run — without executing.
- `tools.py`: Pydantic schemas mirroring the C# `ToolDtos`, argument validation,
  and tolerant tool-call extraction from model output.
- `/chat` now returns ranked **citations** (source name/path/score).
- LLM client: bounded retry with backoff for a warming/loaded local server;
  optional `/embeddings` support.

### Added — security
- `approval.py`: HMAC-SHA256 approval tokens — scoped to (tool, args, user,
  project), single-use via a persistent nonce ledger, time-limited.
- `POST /approvals`: approver-key-gated minting endpoint (human sign-off step).
- Tamper-evident audit chain (`seq` + `prev_hash` + `hash`) with `verify_chain`,
  exposed at `GET /audit/verify` and as `python -m tekla_agent.audit <path>`.
- `screening.py`: NFKC normalisation, Cyrillic→Latin confusable folding,
  zero-width stripping; blocklist patterns in Russian **and** English.
- API auth (`API_KEY`), request-size cap, per-client rate limit, correlation IDs.
- C# workstation host re-verifies the approval signature/expiry/tool with the
  shared secret (`TEKLA_AGENT_APPROVAL_SECRET`) — defence in depth.

### Added — retrieval
- `rag.py`: BM25 Okapi ranking (pure Python), metadata filtering, scored results.

### Added — VRAM-optimised serving
- `configs/models.yaml`: runnable `serving_presets` for 12/16/24 GB (context,
  `-ngl`, `q8_0` KV-cache, parallelism) + `vram_tier_thresholds`.
- `scripts/serve_model.py`: VRAM autodetection, preset selection, and SHA-256
  model integrity verification against `data/models/manifest.json` before serving.
- `deploy/ollama/Modelfile.*`: ready Ollama Modelfiles per tier.

### Added — docs & tests
- `docs/airgap-readiness-checklist.md`: go/no-go checklist for ИБ/инфраструктуры.
- Updated `docs/security-policy.md`, `.env.example`, `examples/requests.http`.
- Test suite expanded from 4 to 41 tests (approval, audit, rag, screening, tools,
  policy, end-to-end API).

### Changed (breaking)
- `ToolPolicy.check_tool_call` now takes `approval_verified: bool` instead of a
  raw `approval_token` string — verification moved to the cryptographic layer.
- Audit records gain `seq`/`prev_hash`/`hash`; old flat logs are not chain-valid.
