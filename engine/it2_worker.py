"""IndicTrans2 translation worker. Runs in its own environment (.venv-indictrans: torch + transformers 4.46, which the
model's own code needs) and talks JSON lines over stdin/stdout, so the engine keeps its newer transformers.
Request: {"texts": [...], "src": "eng_Latn", "tgt": "hin_Deva"}  Reply: {"out": [...]} or {"error": "..."}.
Start with `python -I engine/it2_worker.py <models dir>`; -I keeps engine/ modules from shadowing the standard library."""
import json
import sys
from pathlib import Path

import torch
from IndicTransToolkit.processor import IndicProcessor
from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

ROOT = Path(sys.argv[1])
_models: dict[str, tuple] = {}
_ip = IndicProcessor(inference=True)


def _model(direction: str):
    if direction not in _models:
        path = str(ROOT / f"{direction}-dist-200M")
        tok = AutoTokenizer.from_pretrained(path, trust_remote_code=True)
        model = AutoModelForSeq2SeqLM.from_pretrained(path, trust_remote_code=True).eval()
        _models[direction] = (tok, model)
    return _models[direction]


def translate(texts: list[str], src: str, tgt: str) -> list[str]:
    tok, model = _model("en-indic" if src == "eng_Latn" else "indic-en")
    batch = _ip.preprocess_batch(texts, src_lang=src, tgt_lang=tgt)
    enc = tok(batch, truncation=True, padding="longest", return_tensors="pt")
    with torch.no_grad():
        out = model.generate(**enc, num_beams=4, max_length=256, use_cache=True)
    dec = tok.batch_decode(out, skip_special_tokens=True, clean_up_tokenization_spaces=True)
    return _ip.postprocess_batch(dec, lang=tgt)


def main():
    torch.set_num_threads(4)
    for line in sys.stdin:
        try:
            req = json.loads(line)
            reply = {"out": translate(req["texts"], req["src"], req["tgt"])}
        except Exception as e:  # one bad request must not stop the worker
            reply = {"error": f"{type(e).__name__}: {e}"}
        sys.stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
