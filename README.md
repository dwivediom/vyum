# Vyum

Offline desktop app that reads long documents, compares them, and answers with page-level citations.
It says "not found" instead of guessing. Every component is MIT or Apache-2.0.

## Setup (once, needs internet)

```bash
brew install uv ollama
uv venv --python 3.12 .venv
uv pip install --python .venv -r requirements.txt
scripts/setup_models.sh
cd app && npm install
```

## Run the desktop app

```bash
cd app && npm run tauri dev
```

The Tauri shell starts the engine (`engine/`) on 127.0.0.1 with a random port and a per-launch token.

## Run the engine alone

```bash
VYUM_TOKEN=dev .venv/bin/python -m engine.main --port 8765
```

## Tests and evals

```bash
.venv/bin/python -m pytest tests
.venv/bin/python evals/make_docs.py
.venv/bin/python evals/run_eval.py --url http://127.0.0.1:8765 --token dev --ingest
```

`evals/run_eval.py` reports faithfulness, coverage, abstention accuracy and citation accuracy.

## Upgrading models

Edit `engine/config.py` (or set `VYUM_GEN_MODEL`, `VYUM_VISION_MODEL`, `VYUM_EMBED_MODEL`, `VYUM_LLM_URL`).
The engine talks to any OpenAI-compatible server: Ollama, llama.cpp `llama-server`, or vLLM.
