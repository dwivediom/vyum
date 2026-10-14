"""OCR that reads Indian scripts. The default RapidOCR model reads Latin and Chinese; Hindi comes out as Latin gibberish.
Each page is read with the default model first, and when that reads poorly, with the Indian-script models (PP-OCRv5,
Apache-2.0); the reading with the best confidence wins."""
import threading
from functools import lru_cache

from . import config

_lock = threading.Lock()
SCRIPTS = {  # setting name -> RapidOCR recognition model
    "devanagari": ("DEVANAGARI", "PPOCRV5"), "ta": ("TA", "PPOCRV5"), "te": ("TE", "PPOCRV5"), "kn": ("KA", "PPOCRV4"),
}


@lru_cache(maxsize=8)
def _engine(script: str | None):
    from rapidocr import RapidOCR
    from rapidocr.utils.typings import LangRec, ModelType, OCRVersion
    params = {"Global.log_level": "error"}
    if config.OCR_MODELS:  # installed app: models live in Vyum's data folder (the app's own folder is read-only)
        config.OCR_MODELS.mkdir(parents=True, exist_ok=True)
        params["Global.model_root_dir"] = str(config.OCR_MODELS)
    if script:
        lang_name, version = SCRIPTS[script]
        params |= {"Rec.lang_type": getattr(LangRec, lang_name), "Rec.ocr_version": getattr(OCRVersion, version),
                   "Rec.model_type": ModelType.MOBILE}
    return RapidOCR(params=params)


def _read(img, script: str | None) -> tuple[str, float]:
    with _lock:
        out = _engine(script)(img)
    txts, scores = list(out.txts or []), list(out.scores or [])
    if not txts:
        return "", 0.0
    weight = sum(len(t) for t in txts) or 1
    return "\n".join(txts), sum(s * len(t) for s, t in zip(scores, txts)) / weight


def read_image(img) -> tuple[str, float, str]:
    """(text, confidence, script) for a page image (path, bytes or PIL image)."""
    import numpy as np
    from PIL import Image
    if not isinstance(img, (str, bytes)):
        img = np.array(img.convert("RGB")) if isinstance(img, Image.Image) else img
    best = (*_read(img, None), "latin")
    if best[1] >= 0.88:
        return best
    for script in config.OCR_SCRIPTS:
        text, score = _read(img, script)
        if score > best[1] + 0.03:
            best = (text, score, script)
    return best


def preload():
    """Downloads the Indian-script models once (setup), so OCR works offline afterwards."""
    for s in [None, *config.OCR_SCRIPTS]:
        _engine(s)
