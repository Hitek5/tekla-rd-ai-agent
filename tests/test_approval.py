"""Approval tokens are the boundary to production mutations — test them hard."""

from pathlib import Path

import pytest

from tekla_agent.approval import ApprovalError, ApprovalSigner, NonceLedger
from tekla_agent.tools import validate_args

SECRET = "test-secret-at-least-16-chars-long"
ARGS = {"start": {"x": 0, "y": 0, "z": 0}, "profile": "HEA300"}


class FakeClock:
    def __init__(self, t: float = 1_000_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


def make_signer(tmp_path: Path, clock=None, ttl: int = 600) -> ApprovalSigner:
    ledger = NonceLedger(tmp_path / "consumed.log")
    return ApprovalSigner(SECRET, ledger, default_ttl_seconds=ttl, clock=clock or FakeClock())


def test_weak_secret_rejected(tmp_path: Path) -> None:
    with pytest.raises(ApprovalError):
        ApprovalSigner("short", NonceLedger(tmp_path / "l.log"))


def test_default_placeholder_secret_rejected(tmp_path: Path) -> None:
    # The shipped config default must never be accepted (forgeable tokens).
    with pytest.raises(ApprovalError):
        ApprovalSigner(
            "change-me-please-set-a-32-char-secret", NonceLedger(tmp_path / "l.log")
        )


def test_consume_false_does_not_burn(tmp_path: Path) -> None:
    # Regression: the nonce must survive verification until the call actually
    # executes, so a policy rejection does not waste a single-use approval.
    signer = make_signer(tmp_path)
    token = signer.mint(
        tool="CreateBeam", args=ARGS, user="ivan", project_id="P1", approver="lead", nonce="n1"
    )
    kwargs = dict(tool="CreateBeam", args=ARGS, user="ivan", project_id="P1")
    assert signer.verify(token, consume=False, **kwargs).valid
    assert signer.verify(token, consume=False, **kwargs).valid  # still not burned
    assert signer.verify(token, consume=True, **kwargs).valid  # now burned
    assert not signer.verify(token, consume=True, **kwargs).valid


def test_valid_token_verifies_once(tmp_path: Path) -> None:
    signer = make_signer(tmp_path)
    token = signer.mint(
        tool="CreateBeam", args=ARGS, user="ivan", project_id="P1", approver="lead", nonce="n1"
    )
    v1 = signer.verify(token, tool="CreateBeam", args=ARGS, user="ivan", project_id="P1")
    assert v1.valid
    # Single-use: the second verification fails (nonce already burned).
    v2 = signer.verify(token, tool="CreateBeam", args=ARGS, user="ivan", project_id="P1")
    assert not v2.valid
    assert v2.reason == "already_used"


def test_token_bound_to_tool(tmp_path: Path) -> None:
    signer = make_signer(tmp_path)
    token = signer.mint(
        tool="CreateBeam", args=ARGS, user="ivan", project_id="P1", approver="lead", nonce="n1"
    )
    verdict = signer.verify(token, tool="DeleteObject", args=ARGS, user="ivan", project_id="P1")
    assert not verdict.valid
    assert verdict.reason == "tool_mismatch"


def test_token_bound_to_args(tmp_path: Path) -> None:
    signer = make_signer(tmp_path)
    token = signer.mint(
        tool="CreateBeam", args=ARGS, user="ivan", project_id="P1", approver="lead", nonce="n1"
    )
    verdict = signer.verify(
        token, tool="CreateBeam", args={"profile": "IPE200"}, user="ivan", project_id="P1"
    )
    assert not verdict.valid
    assert verdict.reason == "args_mismatch"


def test_expired_token_rejected(tmp_path: Path) -> None:
    clock = FakeClock()
    signer = make_signer(tmp_path, clock=clock, ttl=60)
    token = signer.mint(
        tool="CreateBeam", args=ARGS, user="ivan", project_id="P1", approver="lead", nonce="n1"
    )
    clock.t += 61
    verdict = signer.verify(token, tool="CreateBeam", args=ARGS, user="ivan", project_id="P1")
    assert not verdict.valid
    assert verdict.reason == "expired"


def test_tampered_signature_rejected(tmp_path: Path) -> None:
    signer = make_signer(tmp_path)
    token = signer.mint(
        tool="CreateBeam", args=ARGS, user="ivan", project_id="P1", approver="lead", nonce="n1"
    )
    tampered = token[:-2] + ("aa" if not token.endswith("aa") else "bb")
    verdict = signer.verify(tampered, tool="CreateBeam", args=ARGS, user="ivan", project_id="P1")
    assert not verdict.valid
    assert verdict.reason == "bad_signature"


def test_mint_and_verify_with_normalised_args(tmp_path: Path) -> None:
    # Mirrors the server: mint binds to normalised args, /tool-calls verifies
    # against normalised args. Integer coords must survive the round-trip.
    signer = make_signer(tmp_path)
    raw = {
        "start": {"x": 0, "y": 0, "z": 0},
        "end": {"x": 6000, "y": 0, "z": 0},
        "profile": "HEA300",
        "material": "S355",
    }
    _ok, _r, normalised = validate_args("CreateBeam", raw)
    token = signer.mint(
        tool="CreateBeam", args=normalised, user="ivan", project_id="P1",
        approver="lead", nonce="n1",
    )
    # Second validation of the same raw input yields identical normalised args.
    _ok2, _r2, normalised2 = validate_args("CreateBeam", raw)
    verdict = signer.verify(
        token, tool="CreateBeam", args=normalised2, user="ivan", project_id="P1"
    )
    assert verdict.valid


def test_ledger_persists_across_restart(tmp_path: Path) -> None:
    signer = make_signer(tmp_path)
    token = signer.mint(
        tool="CreateBeam", args=ARGS, user="ivan", project_id="P1", approver="lead", nonce="n1"
    )
    assert signer.verify(token, tool="CreateBeam", args=ARGS, user="ivan", project_id="P1").valid
    # New signer instance sharing the same ledger file = simulated restart.
    signer2 = make_signer(tmp_path)
    verdict = signer2.verify(token, tool="CreateBeam", args=ARGS, user="ivan", project_id="P1")
    assert not verdict.valid
    assert verdict.reason == "already_used"
