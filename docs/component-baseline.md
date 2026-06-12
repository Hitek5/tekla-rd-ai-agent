# Component Baseline Corrections

This file captures corrections to the component list from `Компоненты для ИИ.xlsx`.

## Keep

- Ubuntu 24.04 LTS.
- Git, Docker Engine, nginx, curl, archive tools, htop, vim or another approved editor.
- PyTorch, Transformers, PEFT, TRL, Accelerate, Datasets.
- llama.cpp, Ollama, Qdrant, Docling.

## Adjust

- Python: use `3.12` or `3.13` for the first deployment. Avoid making `3.14` the default until all ML packages, bitsandbytes, Unsloth, and serving engines are validated in the closed contour.
- CUDA Toolkit: do not pin only to the latest number. Pin to the CUDA runtime supported by the chosen PyTorch wheel and GPU. For Blackwell cards, validate CUDA 12.8/13.x support before installation.
- BitsAndBytes and Unsloth: install only after a GPU-specific smoke test.
- GGUF-my-repo and GGUF-my-LoRA: treat as internet-side preparation tools only. They are not available inside the closed contour unless mirrored or replaced with local llama.cpp conversion.
- Portainer: optional. In stricter environments, prefer CLI plus documented compose files.

## Clarify hardware names

- RTX A2000 is normally 6 GB or 12 GB. Treat "A2000 24 GB" as "two 12 GB cards" unless procurement confirms a different model.
- NVIDIA lists RTX 5070 as 12 GB and RTX 5070 Ti as 16 GB. Confirm whether the available card is 5070 or 5070 Ti.
- Two 12 GB GPUs do not behave like one 24 GB GPU for simple inference. Run separate services per GPU unless tensor parallel/distributed training is deliberately configured.

