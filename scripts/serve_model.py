#!/usr/bin/env python
"""Pick a VRAM-appropriate model preset, verify its integrity, and serve it.

Why this exists
---------------
``configs/models.yaml`` used to *describe* which model suits which GPU; a human
then had to translate that into a llama.cpp/Ollama command line with the right
context length and KV-cache flags. That is exactly where 12 GB cards go
out-of-memory in practice — someone copies a 24 GB context length onto a 12 GB
box. This script removes the guesswork:

1. detect total VRAM via ``nvidia-smi``;
2. select the largest preset that fits (``vram_tier_thresholds``);
3. **verify the model file's SHA-256 against the signed manifest** before serving
   — an unverified or substituted model never starts. This is the supply-chain
   control that matters once weights are carried into a closed network on
   removable media;
4. emit (or run) the exact server command with VRAM-budgeted flags.

It prints by default and only launches with ``--run``, so it is safe to inspect.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import yaml


def detect_vram_gb() -> float | None:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    values = [int(x) for x in out.stdout.split() if x.strip().isdigit()]
    if not values:
        return None
    # Use the smallest GPU: presets assume a single device, never a pooled total.
    return min(values) / 1024.0


def select_preset(config: dict, vram_gb: float) -> tuple[str, dict]:
    for tier in config.get("vram_tier_thresholds", []):
        if vram_gb >= float(tier["min_gb"]):
            name = tier["preset"]
            return name, config["serving_presets"][name]
    raise SystemExit(f"No preset fits {vram_gb:.1f} GB VRAM (need >= 10 GB).")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_model(manifest_path: Path, gguf_file: str, model_path: Path) -> None:
    if not manifest_path.exists():
        raise SystemExit(f"Manifest not found: {manifest_path}. Refusing to serve unverified model.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = next(
        (m for m in manifest.get("models", []) if m.get("gguf_file") == gguf_file),
        None,
    )
    if entry is None:
        raise SystemExit(f"No manifest entry for {gguf_file}. Add it with its sha256 before serving.")
    expected = str(entry.get("sha256", "")).lower()
    if not expected or expected == "fill-after-download":
        raise SystemExit(f"Manifest sha256 for {gguf_file} is not set. Fill it after downloading.")
    if not model_path.exists():
        raise SystemExit(f"Model file missing: {model_path}")
    actual = sha256_file(model_path)
    if actual != expected:
        raise SystemExit(
            f"INTEGRITY FAILURE for {gguf_file}:\n  expected {expected}\n  actual   {actual}"
        )
    print(f"[ok] integrity verified: {gguf_file}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("configs/models.yaml"))
    parser.add_argument("--manifest", type=Path, default=Path("data/models/manifest.json"))
    parser.add_argument("--model-dir", type=Path, default=Path("data/models/gguf"))
    parser.add_argument("--engine", choices=["llama.cpp", "ollama"], default="llama.cpp")
    parser.add_argument("--api-key", default="local-dev-key")
    parser.add_argument("--ollama-modelfile", type=Path, default=None,
                        help="Modelfile to import the verified GGUF from (defaults to the preset).")
    parser.add_argument("--ollama-model", default=None,
                        help="Ollama model tag (defaults to the preset).")
    parser.add_argument("--vram-gb", type=float, default=None, help="Override autodetection.")
    parser.add_argument("--skip-verify", action="store_true", help="DANGEROUS: skip integrity check.")
    parser.add_argument("--run", action="store_true", help="Execute instead of printing the command.")
    args = parser.parse_args()

    config = yaml.safe_load(args.config.read_text(encoding="utf-8"))

    vram = args.vram_gb if args.vram_gb is not None else detect_vram_gb()
    if vram is None:
        raise SystemExit("Could not detect VRAM. Pass --vram-gb explicitly.")
    name, preset = select_preset(config, vram)
    print(f"[info] {vram:.1f} GB VRAM -> preset '{name}' ({preset['model']})", file=sys.stderr)

    if not args.skip_verify:
        verify_model(args.manifest, preset["gguf_file"], args.model_dir / preset["gguf_file"])

    if args.engine == "llama.cpp":
        # llama.cpp serves the verified GGUF file directly.
        command = preset["llama_cpp"].format(
            model_dir=args.model_dir,
            gguf_file=preset["gguf_file"],
            gpu_layers=preset["gpu_layers"],
            context_length=preset["context_length"],
            kv_cache_type=preset["kv_cache_type"],
            parallel=preset["parallel"],
            api_key=args.api_key,
        )
        argv = shlex.split(command)
        if args.run:
            print(f"[run] {command}", file=sys.stderr)
            raise SystemExit(subprocess.call(argv))
        print(command)
        return

    # ollama: `ollama serve` alone would serve whatever model is already
    # installed, defeating the SHA-256 guarantee. So we first import the VERIFIED
    # GGUF into ollama under a known tag (`ollama create -f <Modelfile>`, whose
    # FROM points at the verified file), then serve. The Modelfile is required —
    # we never silently fall back to the daemon's existing state.
    modelfile = args.ollama_modelfile or (
        Path(preset["ollama_modelfile"]) if preset.get("ollama_modelfile") else None
    )
    if modelfile is None:
        raise SystemExit(
            f"Preset '{name}' has no ollama_modelfile; pass --ollama-modelfile so the "
            "verified GGUF is the model that gets served."
        )
    tag = args.ollama_model or preset.get("ollama_tag") or f"tekla-{name}"
    extra_env = {
        "OLLAMA_NUM_PARALLEL": str(preset["parallel"]),
        "OLLAMA_CONTEXT_LENGTH": str(preset["ollama_num_ctx"]),
    }
    env_prefix = " ".join(f"{k}={v}" for k, v in extra_env.items())

    # Ollama resolves a Modelfile `FROM ./file.gguf` relative to the Modelfile's
    # own location, NOT --model-dir. To guarantee it imports the VERIFIED file, we
    # generate a Modelfile whose FROM is the absolute path of the verified GGUF,
    # copying every other directive from the bundled Modelfile.
    verified_gguf = (args.model_dir / preset["gguf_file"]).resolve()
    generated = args.model_dir / f"Modelfile.{tag}.generated"

    def write_generated() -> Path:
        kept = [
            line
            for line in modelfile.read_text(encoding="utf-8").splitlines()
            if not line.strip().upper().startswith("FROM ")
        ]
        generated.write_text(
            f"FROM {verified_gguf}\n" + "\n".join(kept) + "\n", encoding="utf-8"
        )
        return generated

    command = (
        f"ollama create {tag} -f {generated} (FROM {verified_gguf}) "
        f"&& {env_prefix} ollama serve"
    )

    if args.run:
        path = write_generated()
        print(f"[run] ollama create {tag} -f {path}", file=sys.stderr)
        rc = subprocess.call(["ollama", "create", tag, "-f", str(path)])
        if rc != 0:
            raise SystemExit(rc)
        print(f"[run] {env_prefix} ollama serve", file=sys.stderr)
        raise SystemExit(subprocess.call(["ollama", "serve"], env={**os.environ, **extra_env}))
    print(command)


if __name__ == "__main__":
    main()
