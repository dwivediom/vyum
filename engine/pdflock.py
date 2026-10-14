"""pdfium is not thread-safe: two threads in it at once crash the engine. Every pdfium call in Vyum takes this lock,
the same one Docling takes for its own pdfium calls (so indexing a PDF and reading one never overlap)."""
import threading

try:
    from docling.utils.locks import pypdfium2_lock as PDFIUM
except Exception:  # Docling missing: a lock of our own still keeps Vyum's calls apart
    PDFIUM = threading.Lock()
