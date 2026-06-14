"""Tamper-evident, append-only audit log.

The MVP wrote independent JSONL lines. That is fine for debugging but unacceptable
for a security pilot: anyone with file access could delete or edit a line and
leave no trace. Both Russian ИБ requirements (целостность журналов событий) and
international guidance (NIST SP 800-92 tamper-evident logging) expect the log to
*detect* modification.

We add a hash chain: every record carries a monotonic ``seq`` and the
``prev_hash`` of the previous record. Each record's own ``hash`` covers its
content **and** ``prev_hash``. Editing, reordering, or deleting any line breaks
the chain from that point on, and :func:`verify_chain` pinpoints where.

Crucially, the chain hash is an **HMAC keyed by a secret** when one is supplied.
A plain SHA-256 chain only detects accidental corruption: an actor with write
access to the JSONL could rewrite a record and recompute every following hash.
With HMAC, that actor cannot forge the chain without the key (held in the
orchestrator's env, not in the writable log). Pass ``hmac_key=None`` for the
legacy plain-SHA-256 behaviour (dev only).

This needs no database and no network — it is a pure local file, which is exactly
what an air-gapped host can rely on.
"""

from __future__ import annotations

import hashlib
import hmac
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


class AuditIntegrityError(RuntimeError):
    """Raised when the audit log on disk contradicts its head checkpoint."""


def _record_hash(record_without_hash: dict[str, Any], hmac_key: bytes | None = None) -> str:
    payload = json.dumps(
        record_without_hash, ensure_ascii=False, sort_keys=True, default=str
    ).encode("utf-8")
    if hmac_key:
        return "hmac-sha256:" + hmac.new(hmac_key, payload, hashlib.sha256).hexdigest()
    return "sha256:" + hashlib.sha256(payload).hexdigest()


class AuditLogger:
    """Append-only logger that chains records by hash.

    Thread-safe: a lock serialises the read-tail / compute / append sequence so
    concurrent FastAPI requests cannot interleave and fork the chain.
    """

    def __init__(self, path: Path, hmac_key: bytes | None = None):
        self.path = path
        self._hmac_key = hmac_key
        # Head checkpoint sidecar: the latest (seq, hash) is mirrored here on every
        # write. verify_chain compares against it to catch tail truncation / rewind
        # (a valid older prefix that on its own would pass the chain check). For
        # full assurance the checkpoint should also be exported to external WORM /
        # SIEM, since an attacker with write access could truncate both files.
        self.head_path = path.with_suffix(path.suffix + ".head")
        self._lock = Lock()
        self._seq, self._last_hash = self._recover_tail()

        # Fail closed if the log was truncated/rewound WHILE STOPPED: the existing
        # checkpoint would otherwise be silently overwritten by the next write,
        # destroying the only evidence. A checkpoint AHEAD of the recovered tail
        # (or a hash mismatch at the same seq) means the on-disk log lost records.
        cp = read_checkpoint(path)
        if cp is None and self._seq > 0:
            # A non-empty log with NO readable checkpoint means the .head sidecar
            # was deleted or corrupted — treat as tampering, not "no checkpoint".
            # Otherwise the next write() would silently recreate a head from the
            # (possibly already-truncated) tail, destroying the evidence.
            raise AuditIntegrityError(
                f"Audit log {path} has {self._seq} record(s) but its head "
                "checkpoint is missing or corrupt — possible tampering. Investigate; "
                "restore the .head from backup or re-baseline explicitly."
            )
        if cp is not None and (
            cp["seq"] > self._seq
            or (cp["seq"] == self._seq and cp["hash"] != self._last_hash)
        ):
            raise AuditIntegrityError(
                f"Audit log {path} contradicts its checkpoint "
                f"(checkpoint seq {cp['seq']}, recovered seq {self._seq}). "
                "The log was likely truncated or rewound while the service was "
                "stopped. Investigate before restarting; do not delete the .head file."
            )

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
            record_hash = _record_hash(body, self._hmac_key)
            record = {**body, "hash": record_hash}

            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")

            self.head_path.write_text(
                json.dumps({"seq": seq, "hash": record_hash}), encoding="utf-8"
            )

            self._seq = seq
            self._last_hash = record_hash
            return record


def read_checkpoint(path: Path) -> dict[str, Any] | None:
    """Read the head checkpoint sidecar for an audit log, if present."""
    head_path = path.with_suffix(path.suffix + ".head")
    if not head_path.exists():
        return None
    try:
        data = json.loads(head_path.read_text(encoding="utf-8"))
        return {"seq": int(data["seq"]), "hash": str(data["hash"])}
    except (ValueError, KeyError, TypeError, OSError):
        return None


def verify_chain(
    path: Path,
    hmac_key: bytes | None = None,
    expected_head: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate an audit file's hash chain.

    Pass the same ``hmac_key`` the log was written with; without it, an HMAC chain
    will (correctly) fail to verify. Pass ``expected_head`` (``{"seq", "hash"}``,
    e.g. from :func:`read_checkpoint` or an external anchor) to also catch
    tail-truncation / rewind: an older valid prefix verifies on its own, but its
    head will be behind the checkpoint. Returns a report with ``ok`` plus, on
    failure, the first offending sequence number and a human-readable reason. Safe
    to run on a copy exported from the air-gapped host for external (ИБ) review.
    """
    if not path.exists():
        if expected_head and int(expected_head.get("seq", 0)) > 0:
            return {"ok": False, "records": 0, "reason": "truncated: log missing but checkpoint exists"}
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
            if _record_hash(body, hmac_key) != stored_hash:
                return {
                    "ok": False,
                    "records": count,
                    "line": lineno,
                    "reason": "tampered_record: hash mismatch",
                }
            prev_hash = stored_hash
            count += 1

    # Chain is internally consistent; now check it has not been truncated/rewound
    # to an older valid prefix by comparing the head against the checkpoint.
    if expected_head:
        exp_seq = int(expected_head.get("seq", 0))
        exp_hash = str(expected_head.get("hash", ""))
        if count < exp_seq or (count == exp_seq and prev_hash != exp_hash):
            return {
                "ok": False,
                "records": count,
                "reason": f"truncated_or_rewound: head seq {count} behind checkpoint seq {exp_seq}",
            }

    return {"ok": True, "records": count, "head_hash": prev_hash}


if __name__ == "__main__":  # pragma: no cover - CLI for ИБ review
    import os
    import sys

    target = Path(sys.argv[1] if len(sys.argv) > 1 else "data/audit/orchestrator.jsonl")
    # Provide the HMAC key (AUDIT_HMAC_KEY, else APPROVAL_SECRET) to verify a keyed
    # chain; omit for a legacy plain-SHA-256 log.
    key = os.environ.get("AUDIT_HMAC_KEY") or os.environ.get("APPROVAL_SECRET") or ""
    report = verify_chain(
        target,
        key.encode("utf-8") if key else None,
        expected_head=read_checkpoint(target),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["ok"] else 1)
