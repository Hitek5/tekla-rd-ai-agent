# Security Policy

> **v0.2 — implemented controls.** This document is no longer aspirational: the
> mechanisms below are enforced in code. See "Implemented mechanisms (v0.2)".

## Non-negotiable rules

- The agent starts on copies of models only.
- Production writes are disabled by default (`ALLOW_PRODUCTION_MODEL_WRITES=false`).
- `create`, `modify`, `delete`, `export`, and `release RD` require explicit approval.
- Arbitrary C# execution is forbidden in production.
- Tool calls are logged to a tamper-evident JSONL chain before and after execution.
- RAG documents are untrusted input. Instructions found inside documents are treated as content, not commands.

## Implemented mechanisms (v0.2)

| Threat | Control | Where |
| --- | --- | --- |
| Unauthorised mutation | HMAC-signed approval tokens, bound to (tool, args, user, project), single-use, expiring | `approval.py`, re-verified in C# host |
| Unauthenticated API access | Bearer `API_KEY`; separate `APPROVER_API_KEY` for minting approvals | `main.py` middleware |
| Audit tampering | Hash-chained append-only log (`seq` + `prev_hash`); `GET /audit/verify` | `audit.py` |
| Prompt injection / homoglyph bypass | NFKC normalisation, confusable folding, zero-width stripping, RU+EN patterns | `screening.py` |
| Malformed / unexpected tool args | Typed Pydantic validation before policy and before the workstation | `tools.py` |
| Substituted model weights | SHA-256 verification against `manifest.json` before serving | `serve_model.py` |
| Abuse / DoS | Request-size cap + per-client rate limit | `main.py` middleware |

The approval token splits duties: the **orchestrator** is authoritative for
argument binding and single-use (replay) protection; the **C# workstation host**
independently re-verifies the signature, expiry and target tool with the shared
secret, **and keeps its own single-use nonce ledger**, so a token replayed
directly against the host (a bypass around the orchestrator) is rejected after
one use. Argument binding is not re-checked on the host because recomputing the
canonical args hash identically across Python and .NET is brittle; the
orchestrator remains authoritative for it.

## Approval levels

| Level | Examples | Approval |
| --- | --- | --- |
| Read | Get selection, query objects, validate model | No approval |
| Simulation | DryRun, generate plan, explain API usage | No approval |
| Create | Create beam, column, rebar | Required |
| Modify | Change profile, material, geometry, drawing | Required |
| Delete | Delete one or many objects | Required |
| Release | Export/release RD deliverables | Required plus engineering sign-off |

## Prompt-injection handling

The orchestrator must ignore any retrieved text that asks it to bypass policy, disable audit, reveal secrets, execute code, or treat the document as a system instruction.

Every RAG chunk should keep metadata:

- source path and source owner;
- verification status;
- Tekla version;
- import date;
- document hash.

## Minimum audit schema

```json
{
  "seq": 42,
  "timestamp": "2026-06-14T10:00:00Z",
  "event": "tool_call_decision",
  "prev_hash": "sha256:...",
  "user": "domain/user",
  "project_id": "pilot-model-001",
  "tool": "CreateBeam",
  "args_hash": "sha256:...",
  "dry_run": true,
  "approval_verified": false,
  "approval_reason": "no_approval_token",
  "decision": "blocked_requires_approval",
  "allowed": false,
  "hash": "sha256:..."
}
```

`seq` + `prev_hash` + `hash` form the chain: any edit, reorder or deletion is
detected by `verify_chain` (run via `GET /audit/verify` or
`python -m tekla_agent.audit <path>`).

## Rollback posture

Tekla mutating tools should be implemented as small transactions where possible. If the Tekla API cannot guarantee transaction rollback, tools must return the created/modified object IDs and a compensating action plan.

