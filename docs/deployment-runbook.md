# Deployment Runbook

## Phase 0: workstation and server inventory

Record exact hardware before installing:

- CPU: i9-12900;
- RAM: 64 GB DDR4;
- GPU: RTX A2000 12 GB, two RTX A2000 12 GB, RTX 5070 12 GB, or RTX 5070 Ti 16 GB;
- SSD/HDD layout;
- NVIDIA driver version;
- Tekla Structures version;
- Windows/.NET Framework version on workstations.

## Phase 1: GPU server base

1. Install Ubuntu 24.04 LTS.
2. Install NVIDIA production driver appropriate for the GPU.
3. Install Docker Engine and NVIDIA Container Toolkit.
4. Install Python 3.12 or 3.13.
5. Install PyTorch from a wheel matching the CUDA runtime actually supported by the selected GPU and driver.
6. Create internal service DNS names, for example `tekla-ai.internal`.
7. Put nginx in front of orchestrator with internal TLS.

Use `scripts/bootstrap_ubuntu_gpu.sh` as a reviewed starting point, not as an unattended production installer.

## Phase 2: package and model mirrors

Prepare before disconnecting from the internet:

- OCI images for qdrant, ollama, nginx, orchestrator base images;
- PyPI wheels from `pyproject.toml`;
- NuGet packages for the Tekla workstation host;
- Hugging Face model snapshots and GGUF files;
- Git mirrors for public reference repos.

Use `scripts/prepare_airgap_bundle.ps1` to create a transfer bundle.

## Phase 3: MVP services

1. Copy `.env.example` to `.env`.
2. Set `LLM_BASE_URL`, `LLM_MODEL`, `RAG_CHUNKS_PATH`, and `AUDIT_LOG_PATH`.
3. Run `scripts/chunk_corpus.py` on initial docs.
4. Start local LLM server.
5. Start orchestrator.
6. Confirm `/health`.
7. Run `scripts/run_eval.py` on `configs/eval-tasks.example.jsonl`.

## Phase 4: Tekla pilot

1. Install `TeklaWorkstationHost` on one workstation.
2. Run it against an open test model.
3. Enable only read tools.
4. Add create tools in dry-run.
5. Enable approved create tools on copied models.
6. Keep production write disabled until pilot review signs off.

