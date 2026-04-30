#!/usr/bin/env bash
# Download MuseTalk checkpoints into third_party/MuseTalk/models (idempotent).
# Requires: backend .venv with `hf` (huggingface_hub) and gdown: pip install huggingface_hub gdown
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
MT="$ROOT/third_party/MuseTalk"
cd "$MT"
mkdir -p models/musetalk models/musetalkV15 models/syncnet models/dwpose models/face-parse-bisent models/sd-vae models/whisper

if ! command -v hf >/dev/null 2>&1; then
  echo "Install Hugging Face CLI: pip install -U huggingface_hub  (then use \`hf\`)"
  exit 1
fi

unset HF_ENDPOINT || true

hf download TMElyralab/MuseTalk musetalk/musetalk.json musetalk/pytorch_model.bin --local-dir models
hf download TMElyralab/MuseTalk musetalkV15/musetalk.json musetalkV15/unet.pth --local-dir models
hf download stabilityai/sd-vae-ft-mse config.json diffusion_pytorch_model.bin --local-dir models/sd-vae
hf download openai/whisper-tiny config.json pytorch_model.bin preprocessor_config.json --local-dir models/whisper
hf download yzd-v/DWPose dw-ll_ucoco_384.pth --local-dir models/dwpose
hf download ByteDance/LatentSync latentsync_syncnet.pt --local-dir models/syncnet

gdown 'https://drive.google.com/uc?id=154JgKpzCPW82qINcVieuPH3fZ2e0P812' -O models/face-parse-bisent/79999_iter.pth
curl -fsSL -o models/face-parse-bisent/resnet18-5c106cde.pth https://download.pytorch.org/models/resnet18-5c106cde.pth

echo "Weights OK under $MT/models"
find models -type f ! -path '*/.cache/*' | sort
