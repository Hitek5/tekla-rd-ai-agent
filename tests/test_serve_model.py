"""Model-manifest integrity: hash check + optional HMAC signature."""

import hashlib
import json
from pathlib import Path

import pytest

from serve_model import _manifest_signature, verify_model


def _write_model(tmp_path: Path, content: bytes = b"weights") -> tuple[Path, str]:
    gguf = tmp_path / "model.gguf"
    gguf.write_bytes(content)
    return gguf, hashlib.sha256(content).hexdigest()


def test_unsigned_manifest_hash_ok(tmp_path: Path) -> None:
    gguf, sha = _write_model(tmp_path)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"models": [{"gguf_file": "model.gguf", "sha256": sha}]}))
    # No key -> hash-only check passes (warning printed, no raise).
    verify_model(manifest, "model.gguf", gguf)


def test_signed_manifest_requires_valid_signature(tmp_path: Path) -> None:
    gguf, sha = _write_model(tmp_path)
    models = [{"gguf_file": "model.gguf", "sha256": sha}]
    key = b"manifest-signing-key-not-on-media"
    manifest = tmp_path / "manifest.json"

    # Unsigned manifest with a key configured -> reject.
    manifest.write_text(json.dumps({"models": models}))
    with pytest.raises(SystemExit):
        verify_model(manifest, "model.gguf", gguf, manifest_key=key)

    # Correctly signed -> accepted.
    manifest.write_text(json.dumps({"models": models, "signature": _manifest_signature(models, key)}))
    verify_model(manifest, "model.gguf", gguf, manifest_key=key)

    # Tampered models list (different hash) invalidates the signature.
    bad = [{"gguf_file": "model.gguf", "sha256": "0" * 64}]
    manifest.write_text(json.dumps({"models": bad, "signature": _manifest_signature(models, key)}))
    with pytest.raises(SystemExit):
        verify_model(manifest, "model.gguf", gguf, manifest_key=key)
