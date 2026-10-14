#!/usr/bin/env bash
# Optional: IndicTrans2 (AI4Bharat, MIT) for faster, more natural Hindi and Indian-language answers. Needs internet once.
# The models are gated on Hugging Face. Before running this, the owner must:
#   1. sign in at huggingface.co and accept the terms on both pages:
#        https://huggingface.co/ai4bharat/indictrans2-en-indic-dist-200M   (English -> Indian languages: answers)
#        https://huggingface.co/ai4bharat/indictrans2-indic-en-dist-200M   (Indian languages -> English: questions, checks)
#   2. run `.venv/bin/hf auth login`.
# Each direction is used as soon as its model is on disk; without them Vyum translates with the local LLM.
# IndicTrans2's model code needs transformers 4.x, so it gets its own environment (.venv-indictrans) and runs as a
# worker process (engine/it2_worker.py). Its toolkit pulls BSD-licensed helpers (morfessor, sacremoses deps, sphinx).
set -euo pipefail
cd "$(dirname "$0")/.."

[ -x .venv-indictrans/bin/python ] || uv venv --python 3.12 .venv-indictrans
torch_version=$(.venv/bin/python -c 'import torch; print(torch.__version__.split("+")[0])')
uv pip install --python .venv-indictrans/bin/python "torch==$torch_version" "transformers==4.46.3" "IndicTransToolkit>=1.0" sentencepiece

dest="${VYUM_INDICTRANS_DIR:-$HOME/.cache/vyum/indictrans2}"
for d in en-indic indic-en; do
  .venv/bin/hf download "ai4bharat/indictrans2-$d-dist-200M" --local-dir "$dest/$d-dist-200M" \
    || echo "Could not download $d: accept the terms on its Hugging Face page, then run this again."
done

.venv/bin/python - <<'PY'
from engine import translate
print("answers:", translate.backend("en-indic"), "| questions:", translate.backend("indic-en"))
print(translate.answer_sentences(["Late payments carry interest of 1.5% per month [2]."], "hi"))
PY
