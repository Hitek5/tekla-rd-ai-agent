from pathlib import Path

from tekla_agent.audit import AuditLogger, verify_chain


def test_chain_verifies(tmp_path: Path) -> None:
    log = tmp_path / "audit.jsonl"
    logger = AuditLogger(log)
    logger.write("event_a", value=1)
    logger.write("event_b", value=2)
    logger.write("event_c", value=3)

    report = verify_chain(log)
    assert report["ok"]
    assert report["records"] == 3


def test_chain_continues_after_restart(tmp_path: Path) -> None:
    log = tmp_path / "audit.jsonl"
    AuditLogger(log).write("first", value=1)
    # New logger instance must pick up seq/prev_hash from disk.
    AuditLogger(log).write("second", value=2)
    report = verify_chain(log)
    assert report["ok"]
    assert report["records"] == 2


def test_tampering_is_detected(tmp_path: Path) -> None:
    log = tmp_path / "audit.jsonl"
    logger = AuditLogger(log)
    logger.write("event_a", value=1)
    logger.write("event_b", value=2)

    lines = log.read_text(encoding="utf-8").splitlines()
    # Flip a value in the first record without recomputing its hash.
    lines[0] = lines[0].replace('"value": 1', '"value": 999')
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")

    report = verify_chain(log)
    assert not report["ok"]
    assert report["line"] == 1


def test_hmac_chain_resists_recompute(tmp_path: Path) -> None:
    # An actor with file write but no key cannot forge a valid HMAC chain.
    import hashlib
    import json as _json

    key = b"super-secret-audit-key-0123456789"
    log = tmp_path / "audit.jsonl"
    logger = AuditLogger(log, hmac_key=key)
    logger.write("e1", x=1)
    logger.write("e2", x=2)
    assert verify_chain(log, hmac_key=key)["ok"]

    lines = log.read_text(encoding="utf-8").splitlines()
    rec = _json.loads(lines[1])
    rec["x"] = 999  # tamper
    body = {k: v for k, v in rec.items() if k != "hash"}
    payload = _json.dumps(body, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    rec["hash"] = "sha256:" + hashlib.sha256(payload).hexdigest()  # attacker recompute, no key
    lines[1] = _json.dumps(rec, ensure_ascii=False, sort_keys=True)
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")

    report = verify_chain(log, hmac_key=key)
    assert not report["ok"]
    assert report["reason"].startswith("tampered_record")


def test_hmac_chain_fails_without_key(tmp_path: Path) -> None:
    key = b"super-secret-audit-key-0123456789"
    log = tmp_path / "audit.jsonl"
    AuditLogger(log, hmac_key=key).write("e1", x=1)
    assert not verify_chain(log)["ok"]  # plain SHA-256 cannot reproduce the HMAC


def test_tail_truncation_detected_via_checkpoint(tmp_path: Path) -> None:
    from tekla_agent.audit import read_checkpoint

    log = tmp_path / "audit.jsonl"
    logger = AuditLogger(log)
    logger.write("e1", x=1)
    logger.write("e2", x=2)
    logger.write("e3", x=3)

    # Attacker drops the newest record — the remaining prefix is still internally
    # consistent, so the plain chain check passes...
    lines = log.read_text(encoding="utf-8").splitlines()
    log.write_text("\n".join(lines[:2]) + "\n", encoding="utf-8")
    assert verify_chain(log)["ok"] is True

    # ...but the checkpoint reveals the head is behind.
    report = verify_chain(log, expected_head=read_checkpoint(log))
    assert report["ok"] is False
    assert report["reason"].startswith("truncated_or_rewound")


def test_non_object_line_reported_not_crashed(tmp_path: Path) -> None:
    log = tmp_path / "audit.jsonl"
    log.write_text('"just a string, not an object"\n', encoding="utf-8")
    report = verify_chain(log)
    assert not report["ok"]
    assert report["reason"] == "not_an_object"


def test_bad_seq_reported_not_crashed(tmp_path: Path) -> None:
    log = tmp_path / "audit.jsonl"
    log.write_text('{"seq": "abc", "prev_hash": "x", "hash": "y"}\n', encoding="utf-8")
    report = verify_chain(log)
    assert not report["ok"]
    assert report["reason"] == "bad_seq"


def test_null_seq_reported_not_crashed(tmp_path: Path) -> None:
    log = tmp_path / "audit.jsonl"
    log.write_text('{"seq": null, "prev_hash": "x", "hash": "y"}\n', encoding="utf-8")
    report = verify_chain(log)
    assert not report["ok"]
    assert report["reason"] == "bad_seq"


def test_deletion_is_detected(tmp_path: Path) -> None:
    log = tmp_path / "audit.jsonl"
    logger = AuditLogger(log)
    logger.write("a", value=1)
    logger.write("b", value=2)
    logger.write("c", value=3)

    lines = log.read_text(encoding="utf-8").splitlines()
    del lines[1]  # remove the middle record
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")

    report = verify_chain(log)
    assert not report["ok"]
