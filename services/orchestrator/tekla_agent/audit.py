"""Tamper-evident, append-only audit log.

The MVP wrote independent JSONL lines. That is fine for debugging but unacceptable
for a security pilot: anyone with file access could delete or edit a line and
leave no trace. Both Russian ИБ requirements (целостность журналов событий) and
international guidance (NIST SP 800-92 tamper-evident logging) expect the log to
*detect* modification.

We add a hash chain: every record carries a monotonic ``seq`` and the SHA-256
``prev_hash`` of the previous record. Each record's own ``hash`` covers its
content **and** ``prev_hash``. Editing, reordering, or deleting any line breaks
the chain from that point on, and :func:`verify_chain` pinpoints where.

This needs no database and no network — it is a pure local file, which is exactly
what an air-gapped host can rely on.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any

GENESIS_HASH = "sha256:" + "0" * 64


def stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _record_hash(record_without_hash: dict[str, Any]) -> str:
    return stable_hash(record_without_hash)


class AuditLogger:
    """Append-only logger that chains records by hash.

    Thread-safe: a lock serialises the read-tail / compute / append sequence so
    concurrent FastAPI requests cannot interleave and fork the chain.
    """

    def __init__(self, path: Path):
        self.path = path
        self._lock = Lock()
        self._seq, self._last_hash = self._recover_tail()

    def _recover_tail(self) -> tuple[int, str]:
        """Read the last valid record to continue the chain after a restart."""
        if not self.path.exists():
            return 0, GENESIS_HASH
        last_seq = 0
        last_hash = GENESIS_HASH
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    last_seq = int(record["seq"])
                    last_hash = str(record["hash"])
                except (ValueError, KeyError, TypeError):
                    # Stop at the first corrupt line; the chain ends here.
                    break
        return last_seq, last_hash

    def write(self, event: str, **fields: Any) -> dict[str, Any]:
        with self._lock:
            seq = self._seq + 1
            body = {
                "seq": seq,
                "timestamp": datetime.now(UTC).isoformat(),
                "event": event,
                "prev_hash": self._last_hash,
                **fields,
            }
            record_hash = _record_hash(body)
            record = {**body, "hash": record_hash}

            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")

            self._seq = seq
            self._last_hash = record_hash
            return record


def verify_chain(path: Path) -> dict[str, Any]:
    """Validate an audit file's hash chain.

    Returns a report with ``ok`` plus, on failure, the first offending sequence
    number and a human-readable reason. Safe to run on a copy exported from the
    air-gapped host for external (ИБ) review.
    """
    if not path.exists():
        return {"ok": True, "records": 0, "reason": "empty"}

    prev_hash = GENESIS_HASH
    expected_seq = 0
    count = 0
    with path.open("r", encoding="utf-8") as handle:
        for lineno, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                return {"ok": False, "records": count, "line": lineno, "reason": "invalid_json"}

            # A syntactically valid line that is not a well-formed audit object
            # (a scalar/list, or a non-integer seq) is a corrupt/tampered record,
            # not a server error — report it rather than raising a 500.
            if not isinstance(record, dict):
                return {"ok": False, "records": count, "line": lineno, "reason": "not_an_object"}

            expected_seq += 1
            try:
                seq = int(record.get("seq"))
            except (TypeError, ValueError):
                return {"ok": False, "records": count, "line": lineno, "reason": "bad_seq"}
            if seq != expected_seq:
                return {
                    "ok": False,
                    "records": count,
                    "line": lineno,
                    "reason": f"seq_gap: expected {expected_seq}, got {record.get('seq')}",
                }
            if record.get("prev_hash") != prev_hash:
                return {
                    "ok": False,
                    "records": count,
                    "line": lineno,
                    "reason": "broken_chain: prev_hash mismatch",
                }
            stored_hash = record.get("hash")
            body = {k: v for k, v in record.items() if k != "hash"}
            if _record_hash(body) != stored_hash:
                return {
                    "ok": False,
                    "records": count,
                    "line": lineno,
                    "reason": "tampered_record: hash mismatch",
                }
            prev_hash = stored_hash
            count += 1

    return {"ok": True, "records": count, "head_hash": prev_hash}


if __name__ == "__main__":  # pragma: no cover - CLI for ИБ review
    import sys

    target = Path(sys.argv[1] if len(sys.argv) > 1 else "data/audit/orchestrator.jsonl")
    report = verify_chain(target)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["ok"] else 1)
