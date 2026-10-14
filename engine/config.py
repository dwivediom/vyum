"""Every model name and threshold lives here. Upgrading Vyum = editing this file (or the env vars)."""
import os
import sys
from pathlib import Path

# Installed app: VYUM_RESOURCES is the folder the installer put the engine, its runtimes and tools in (read-only).
# Unset in a source checkout, where the tools sit next to the repo and the models in the usual caches.
RESOURCES = Path(os.environ["VYUM_RESOURCES"]) if os.environ.get("VYUM_RESOURCES") else None
PACKAGED = RESOURCES is not None
WINDOWS = sys.platform == "win32"
MAC = sys.platform == "darwin"
REPO = Path(__file__).resolve().parents[1]


def _default_data_dir() -> Path:
    if WINDOWS:
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "Vyum"
    if MAC:
        return Path.home() / "Library/Application Support/Vyum"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "Vyum"

# Model server: any OpenAI-compatible endpoint (Ollama in dev, llama-server when shipped, vLLM on GPUs).
LLM_BASE_URL = os.environ.get("VYUM_LLM_URL", "http://127.0.0.1:11434/v1")

# Ollama aliases created by scripts/setup_models.sh (same weights, larger context window).
GEN_MODEL = os.environ.get("VYUM_GEN_MODEL", "vyum-qwen3-8b")          # Qwen3-8B, Apache-2.0
VISION_MODEL = os.environ.get("VYUM_VISION_MODEL", "vyum-qwen25vl-7b")  # Qwen2.5-VL-7B, Apache-2.0
EMBED_MODEL = os.environ.get("VYUM_EMBED_MODEL", "bge-m3")              # BGE-M3, MIT
EMBED_DIM = 1024
RERANK_MODEL = os.environ.get("VYUM_RERANK_MODEL", "BAAI/bge-reranker-v2-m3")  # Apache-2.0

DATA_DIR = Path(os.environ.get("VYUM_DATA", _default_data_dir()))
# Models: an installed app keeps them in its data folder (downloaded once on first launch, see engine/setup.py);
# a source checkout uses the usual caches (scripts/setup_models.sh).
MODELS_DIR = Path(os.environ.get("VYUM_MODELS", DATA_DIR / "models" if PACKAGED else Path.home() / ".cache"))
# Docling layout, table and RapidOCR models.
DOCLING_MODELS = Path(os.environ.get("VYUM_DOCLING_MODELS", MODELS_DIR / ("docling" if PACKAGED else "docling/models")))
# RapidOCR's Indian-script models (None: inside the rapidocr package, as in a source checkout).
OCR_MODELS = Path(os.environ["VYUM_OCR_MODELS"]) if os.environ.get("VYUM_OCR_MODELS") else \
    (MODELS_DIR / "ocr" if PACKAGED else None)

# Ingest
CHUNKER_VERSION = 3  # 3: streamed ingest, two-level heading paths, headers/footers removed, line numbers for text
CHUNK_CHARS = 1100
DOCLING_MAX_PAGES = int(os.environ.get("VYUM_DOCLING_MAX_PAGES", "300"))  # larger PDFs use the fast text-layer reader
PDF_SEGMENT_PAGES = 50       # pages per checkpoint in the fast reader
TEXT_SEGMENT_LINES = 2000    # lines per checkpoint for text files
SCANNED_PAGE_CHARS = 20      # a large-PDF page with less text than this is OCR'd
SUMMARY_DIRECT_MAX = 400     # passages; bigger documents are summarized part by part
SUMMARY_PARTS = 16           # parts a whole-document summary reads
VECTOR_INDEX_MIN_ROWS = int(os.environ.get("VYUM_VECTOR_INDEX_MIN_ROWS", "1000000"))  # exact search is 14 ms at 40k rows
CHUNK_OVERLAP_CHARS = 150
DESCRIBE_FIGURES = os.environ.get("VYUM_DESCRIBE_FIGURES", "1") == "1"
MIN_FIGURE_PX = 120  # skip logos and icons smaller than this

# Retrieval
RETRIEVE_K = 50        # candidates per query from each of BM25 and vectors
RERANK_K = 8           # passages the model sees
RRF_K = 60
COMPARE_PER_DOC_K = 4  # passages per document in compare mode

# Harness
MIN_EVIDENCE_SCORE = 0.15  # best rerank score (0-1) needed to attempt an answer
MAX_REWRITES = 2           # extra retrieval rounds before abstaining
QUERY_REWRITES = 3         # alternative phrasings in Accurate mode
GEN_MAX_TOKENS = 900
THINK_MAX_TOKENS = 3000    # extra budget for the reasoning pass when thinking is on
HISTORY_TURNS = 3          # earlier Q&A pairs used to resolve follow-up questions
MAX_FILE_MB = 200          # project scans skip larger files

# General chat (answers from the model's own knowledge, your files and, when switched on, the web)
CHAT_MAX_TOKENS = 1800       # answers can be long here: code, drafts, explanations
CHAT_HISTORY_TURNS = 6       # earlier messages the model sees
CHAT_DOC_PASSAGES = 5        # passages from your files offered as sources
CHAT_WHOLE_FILE_CHARS = 7000  # attached files this small are read in full instead of searched
# Web search sends only the search query (never document text) and only when the user switches it on.
WEB_RESULTS = 6              # search results considered
WEB_PAGES = 4                # pages fetched and read
WEB_PASSAGES = 6             # page passages offered as sources
WEB_TIMEOUT_S = 8
# A local SearXNG install that the engine starts on first use when nothing answers at the configured address.
SEARXNG_DIR = Path(os.environ.get("VYUM_SEARXNG_DIR", REPO.parent / "searxng"))
SEARXNG_START = REPO / "scripts/start_searxng.sh"  # source checkouts only: SearXNG is AGPL and is not shipped

# Office: AI-created and AI-edited documents, written through GenOffice's engines (its `genoffice` command line).
# GenOffice is built from source (scripts/build_genoffice.sh): no usage analytics and no auto-updater in source builds.
# GenOffice's source checkout (Apache-2.0): its PDF module powers Vyum's PDF editor (see engine/pdfhost.py)
# Installed app: RESOURCES/genoffice holds GenOffice's built app (GenOffice.app on macOS, the unpacked folder with
# GenOffice.exe on Windows) and RESOURCES/pdfhost the PDF editor host, its Electron runtime and renderer.
GENOFFICE_SRC = Path(os.environ.get("VYUM_GENOFFICE_SRC", REPO.parent / "genoffice"))
GENOFFICE_APP = Path(os.environ.get("VYUM_GENOFFICE_APP", (RESOURCES / "genoffice" / ("GenOffice.app" if MAC else "GenOffice"))
                                    if PACKAGED else REPO.parent / "genoffice/apps/shell/release"
                                    / ("mac-arm64/GenOffice.app" if MAC else "win-unpacked")))
# Files Vyum creates go where people look for documents; tests and custom data folders keep them inside the data folder.
OFFICE_DIR = Path(os.environ.get("VYUM_OFFICE_DIR", (DATA_DIR / "office") if os.environ.get("VYUM_DATA")
                                 else Path.home() / "Documents/Vyum"))
OFFICE_MAX_EDIT_CHARS = 40000   # text sent to the model when it plans an edit

# Indian languages: scripts the OCR tries when the default model reads a page poorly (devanagari = Hindi, Marathi,
# Nepali; ta = Tamil; te = Telugu; kn = Kannada). Each adds about a second per poorly read page.
OCR_SCRIPTS = [x for x in os.environ.get("VYUM_OCR_SCRIPTS", "devanagari").split(",") if x]

# IndicTrans2 (AI4Bharat, MIT): English <-> Indian-language translation. Used when both models are in this folder
# (scripts/setup_indictrans.sh); otherwise the local LLM translates, with the same checks.
INDICTRANS_DIR = Path(os.environ.get("VYUM_INDICTRANS_DIR", MODELS_DIR / ("indictrans2" if PACKAGED else "vyum/indictrans2")))
# Python for the IndicTrans2 worker (it needs transformers 4.46; the engine uses 5.x). Installed app: the engine's own
# Python with an overlay folder of the older packages first on its path; source checkout: a separate venv.
INDICTRANS_OVERLAY = RESOURCES / "runtime/it2-overlay" if PACKAGED else None
