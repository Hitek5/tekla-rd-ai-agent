"""Scoped, single-use, time-limited HMAC approval tokens.

In the MVP the approval token was "any non-empty string", which means anyone who
can reach the orchestrator could authorise a model mutation. For a closed-loop
(КСПД) pilot that is the single most dangerous gap: approval is the boundary
between "the agent proposed something" and "the agent changed the production
model".

This module makes an approval token a cryptographic capability that is:

* **bound** to a specific (tool, args, user, project) — a token minted to create
  one beam cannot be replayed to delete an object;
* **time-limited** — it expires (default 10 min), so a leaked token has a short
  blast radius;
* **single-use** — the nonce is burned on first successful use and persisted to
  an append-only ledger so a restart cannot "forget" a spent token;
* **offline-verifiable** — pure HMAC-SHA256 over a shared secret, so both the
  orchestrator and the C# workstation host can verify it without any network
  call or external PKI. This is what keeps it air-gap friendly.

Wire format (compact, URL-safe, no padding)::

    base64url(payload_json) "." base64url(hmac_sha256(secret, payload_json))
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any


def _b64u_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64u_decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def args_fingerprint(args: Any) -> str:
    """Stable SHA-256 of tool arguments, independent of key ordering."""
    payload = json.dumps(args, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ApprovalClaims:
    tool: str
    args_hash: str
    user: str
    project_id: str
    approver: str
    nonce: str
    issued_at: int
    expires_at: int
    # SHA-256 of the exact canonical request body the host will receive. Lets the
    # C# host bind the token to the arguments by hashing the raw bytes it gets,
    # with no cross-language JSON re-serialisation.
    body_sha256: str = ""
    # Target workstation the token is valid for; the orchestrator refuses to send
    # it anywhere else, so a leaked/uncommitted nonce cannot be replayed cross-host.
    workstation_url: str = ""

    def to_payload(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "args_hash": self.args_hash,
            "user": self.user,
            "project_id": self.project_id,
            "approver": self.approver,
            "nonce": self.nonce,
            "iat": self.issued_at,
            "exp": self.expires_at,
            "body_sha256": self.body_sha256,
            "workstation_url": self.workstation_url,
        }


@dataclass(frozen=True)
class ApprovalVerdict:
    valid: bool
    reason: str
    claims: ApprovalClaims | None = None


class ApprovalError(RuntimeError):
    pass


class NonceLedger:
    """Append-only store of consumed approval nonces (replay protection).

    Kept deliberately simple and file-based so it survives restarts inside an
    air-gapped host without a database. The in-memory set is the fast path; the
    file is the durable source of truth replayed on startup.
    """

    def __init__(self, path: Path):
        self.path = path
        self._lock = Lock()
        self._seen: set[str] = set()
        # In-flight reservations: a nonce is reserved before the workstation call
        # and committed (persisted) only once the call is accepted, or rolled back
        # on failure. Reservations are in-memory only — a crash mid-flight simply
        # releases them, which is the safe outcome.
        self._reserved: set[str] = set()
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    self._seen.add(line)

    def is_spent(self, nonce: str) -> bool:
        return nonce in self._seen

    def reserve(self, nonce: str) -> bool:
        """Atomically claim a nonce for an in-flight call.

        Returns False if it is already spent or already reserved — this is what
        blocks two concurrent requests carrying the same approval from both
        executing.
        """
        with self._lock:
            if nonce in self._seen or nonce in self._reserved:
                return False
            self._reserved.add(nonce)
            return True

    def commit(self, nonce: str) -> None:
        """Finalise a reserved nonce as permanently spent (persisted to disk)."""
        with self._lock:
            self._reserved.discard(nonce)
            if nonce in self._seen:
                return
            self._seen.add(nonce)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(nonce + "\n")

    def rollback(self, nonce: str) -> None:
        """Release a reservation so the approval can be retried."""
        with self._lock:
            self._reserved.discard(nonce)

    def burn(self, nonce: str) -> bool:
        """Mark a nonce consumed in one step. Returns False if already spent."""
        with self._lock:
            if nonce in self._seen:
                return False
            self._seen.add(nonce)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(nonce + "\n")
            return True


class ApprovalSigner:
    """Mints and verifies approval tokens against a shared secret."""

    # Well-known placeholder values that must never sign real tokens. Shipping
    # any of these means anyone can forge approvals, so we refuse to start.
    _INSECURE_SECRETS = frozenset(
        {
            "change-me-please-set-a-32-char-secret",
            "change-me",
            "changeme",
            "secret",
            "local-dev-key",
        }
    )

    def __init__(
        self,
        secret: str,
        ledger: NonceLedger,
        *,
        default_ttl_seconds: int = 600,
        clock=time.time,
    ):
        if not secret or len(secret) < 16:
            raise ApprovalError(
                "Approval secret must be at least 16 characters. "
                "Set APPROVAL_SECRET to a strong random value."
            )
        if secret.strip().lower() in self._INSECURE_SECRETS or secret.startswith("change-me"):
            raise ApprovalError(
                "APPROVAL_SECRET is set to a well-known default. Generate a unique "
                "random secret (e.g. `python -c \"import secrets; print(secrets.token_urlsafe(32))\"`) "
                "and set the SAME value as TEKLA_AGENT_APPROVAL_SECRET on the workstation host."
            )
        self._secret = secret.encode("utf-8")
        self._ledger = ledger
        self._default_ttl = default_ttl_seconds
        self._clock = clock

    def _sign(self, payload_bytes: bytes) -> str:
        digest = hmac.new(self._secret, payload_bytes, hashlib.sha256).digest()
        return _b64u_encode(digest)

    def mint(
        self,
        *,
        tool: str,
        args: Any,
        user: str,
        project_id: str,
        approver: str,
        nonce: str,
        ttl_seconds: int | None = None,
        body_sha256: str = "",
        workstation_url: str = "",
    ) -> str:
        now = int(self._clock())
        ttl = ttl_seconds if ttl_seconds is not None else self._default_ttl
        claims = ApprovalClaims(
            tool=tool,
            args_hash=args_fingerprint(args),
            user=user,
            project_id=project_id,
            approver=approver,
            nonce=nonce,
            issued_at=now,
            expires_at=now + ttl,
            body_sha256=body_sha256,
            workstation_url=workstation_url,
        )
        payload_bytes = json.dumps(
            claims.to_payload(), ensure_ascii=False, sort_keys=True
        ).encode("utf-8")
        return f"{_b64u_encode(payload_bytes)}.{self._sign(payload_bytes)}"

    def _parse(self, token: str) -> tuple[ApprovalClaims, bytes] | None:
        if not token or token.count(".") != 1:
            return None
        payload_b64, signature_b64 = token.split(".", 1)
        try:
            payload_bytes = _b64u_decode(payload_b64)
            expected = self._sign(payload_bytes)
        except (ValueError, TypeError):
            return None
        if not hmac.compare_digest(expected, signature_b64):
            return None
        try:
            data = json.loads(payload_bytes)
            claims = ApprovalClaims(
                tool=str(data["tool"]),
                args_hash=str(data["args_hash"]),
                user=str(data["user"]),
                project_id=str(data["project_id"]),
                approver=str(data["approver"]),
                nonce=str(data["nonce"]),
                issued_at=int(data["iat"]),
                expires_at=int(data["exp"]),
                body_sha256=str(data.get("body_sha256", "")),
                workstation_url=str(data.get("workstation_url", "")),
            )
        except (KeyError, ValueError, TypeError):
            return None
        return claims, payload_bytes

    def verify(
        self,
        token: str | None,
        *,
        tool: str,
        args: Any,
        user: str,
        project_id: str,
        workstation_url: str = "",
        consume: bool = True,
    ) -> ApprovalVerdict:
        if not token:
            return ApprovalVerdict(False, "no_approval_token")

        parsed = self._parse(token)
        if parsed is None:
            return ApprovalVerdict(False, "bad_signature")
        claims, _ = parsed

        now = int(self._clock())
        if now >= claims.expires_at:
            return ApprovalVerdict(False, "expired", claims)
        if claims.tool != tool:
            return ApprovalVerdict(False, "tool_mismatch", claims)
        if claims.args_hash != args_fingerprint(args):
            return ApprovalVerdict(False, "args_mismatch", claims)
        if claims.user != user:
            return ApprovalVerdict(False, "user_mismatch", claims)
        if claims.project_id != project_id:
            return ApprovalVerdict(False, "project_mismatch", claims)
        # Reject a token presented for a different workstation than it was minted
        # for (blocks cross-host replay of a leaked/uncommitted nonce). An empty
        # claim is treated as unbound and rejected, never as "matches anything".
        if not claims.workstation_url or claims.workstation_url != workstation_url:
            return ApprovalVerdict(False, "workstation_mismatch", claims)
        if self._ledger.is_spent(claims.nonce):
            return ApprovalVerdict(False, "already_used", claims)

        if consume and not self._ledger.burn(claims.nonce):
            # Lost a race with a concurrent request for the same nonce.
            return ApprovalVerdict(False, "already_used", claims)

        return ApprovalVerdict(True, "approved", claims)

    def reserve(
        self,
        token: str | None,
        *,
        tool: str,
        args: Any,
        user: str,
        project_id: str,
        workstation_url: str = "",
    ) -> ApprovalVerdict:
        """Validate a token and atomically reserve its nonce for an in-flight call.

        Reserving before the workstation call (and committing only on acceptance)
        gives single-use semantics that survive both concurrent duplicates and
        transient transport failures.
        """
        verdict = self.verify(
            token,
            tool=tool,
            args=args,
            user=user,
            project_id=project_id,
            workstation_url=workstation_url,
            consume=False,
        )
        if not verdict.valid or verdict.claims is None:
            return verdict
        if not self._ledger.reserve(verdict.claims.nonce):
            return ApprovalVerdict(False, "already_used", verdict.claims)
        return ApprovalVerdict(True, "reserved", verdict.claims)

    def commit(self, token: str | None) -> None:
        """Finalise a previously reserved token as spent."""
        parsed = self._parse(token) if token else None
        if parsed is not None:
            self._ledger.commit(parsed[0].nonce)

    def rollback(self, token: str | None) -> None:
        """Release a reservation so the approval can be retried."""
        parsed = self._parse(token) if token else None
        if parsed is not None:
            self._ledger.rollback(parsed[0].nonce)
