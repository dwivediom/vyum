#!/usr/bin/env bash
# One-time model setup (needs internet). After this, Vyum runs fully offline.
set -euo pipefail
cd "$(dirname "$0")/.."

ollama pull qwen3:8b        # generator, Apache-2.0
ollama pull qwen2.5vl:7b    # vision, Apache-2.0
ollama pull bge-m3          # embeddings, MIT

# Same weights with an 8K context window, so 8 passages + the answer always fit.
tmp=$(mktemp -d)
printf 'FROM qwen3:8b\nPARAMETER num_ctx 8192\n' > "$tmp/gen"
printf 'FROM qwen2.5vl:7b\nPARAMETER num_ctx 8192\n' > "$tmp/vision"
ollama create vyum-qwen3-8b -f "$tmp/gen"
ollama create vyum-qwen25vl-7b -f "$tmp/vision"
rm -rf "$tmp"

# Reranker (Apache-2.0) and Docling layout/table models (MIT/Apache) into the local Hugging Face cache.
.venv/bin/python - <<'PY'
from huggingface_hub import snapshot_download
snapshot_download("BAAI/bge-reranker-v2-m3")
from docling.utils.model_downloader import download_models
download_models()
# OCR for Indian scripts (PaddleOCR PP-OCRv5 models, Apache-2.0): Hindi/Marathi (devanagari) by default
from engine import ocr
ocr.preload()
print("models ready")
PY
