#!/usr/bin/env bash
set -euo pipefail

# Review before use. This is a baseline, not an unattended production installer.

PYTHON_VERSION="${PYTHON_VERSION:-3.12}"
NVIDIA_DRIVER_PACKAGE="${NVIDIA_DRIVER_PACKAGE:-nvidia-driver-580}"
PYTORCH_INDEX_URL="${PYTORCH_INDEX_URL:-https://download.pytorch.org/whl/cu128}"

sudo apt-get update
sudo apt-get install -y \
  ca-certificates \
  curl \
  git \
  htop \
  jq \
  mc \
  nginx \
  python"${PYTHON_VERSION}" \
  python"${PYTHON_VERSION}"-venv \
  unzip \
  vim \
  zip \
  zstd

sudo apt-get install -y "${NVIDIA_DRIVER_PACKAGE}"

curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER"

distribution=$(. /etc/os-release; echo "$ID$VERSION_ID")
curl -fsSL "https://nvidia.github.io/libnvidia-container/gpgkey" |
  sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -fsSL "https://nvidia.github.io/libnvidia-container/$distribution/libnvidia-container.list" |
  sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' |
  sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt-get update
sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker

python"${PYTHON_VERSION}" -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install torch torchvision torchaudio --index-url "${PYTORCH_INDEX_URL}"
python - <<'PY'
import torch
print({"torch": torch.__version__, "cuda_available": torch.cuda.is_available()})
if torch.cuda.is_available():
    print({"device": torch.cuda.get_device_name(0)})
PY

