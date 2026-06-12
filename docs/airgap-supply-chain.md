# Air-Gap Supply Chain

## Objects to mirror

- Docker images: Qdrant, Ollama or llama.cpp/vLLM/SGLang image, nginx, orchestrator runtime.
- Python wheels: FastAPI stack, optional Qdrant client, ML stack for training host.
- NuGet packages: workstation host dependencies and approved internal packages.
- Models: GGUF and Hugging Face snapshots with checksums.
- Repositories: Tekla examples, MCP references, internal code examples.
- Documentation: exported Tekla docs, RD standards, company instructions.

## Transfer bundle layout

```text
airgap-bundle/
  docker/
    images.txt
    *.tar
  python/
    requirements.lock.txt
    wheels/
  nuget/
    packages/
  models/
    manifest.json
    gguf/
    hf/
  git/
    *.bundle
  checksums/
    SHA256SUMS
```

## Offline environment variables

Set these after the bundle is imported and validated:

```bash
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export HF_DATASETS_OFFLINE=1
```

## Integrity checks

- Hash all transferred files with SHA-256.
- Store model license text beside every model.
- Store exact model revision or commit.
- Reject unsigned or unknown binaries.
- Keep a human-readable import log for security review.

