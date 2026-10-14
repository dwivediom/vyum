"""Turns files into cited passages and writes them to the index as a stream.

Large files never sit in memory whole: readers yield segments (50 PDF pages, ~2,000 text lines), each segment is
embedded and written, then a checkpoint is saved, so an interrupted ingest resumes where it stopped and a document
is searchable while it is still being indexed.

Readers:
- PDF up to DOCLING_MAX_PAGES: Docling (layout, tables, OCR, figures described by the vision model).
- Larger PDFs: the text layer via PDFium (about 1,400 pages/s), with learned page headers and footers removed and
  headings tracked as a two-level path ("SUPPLIER AGREEMENT NO. 05012 › 4. Payment"); pages without text are OCR'd.
- Text and Markdown: streamed line by line; citations point to line numbers.
- Images: described by the vision model.
"""
import hashlib
import io
import re
import shutil
import time
import uuid
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from . import config, lang, llm
from .retrieval import passage_with_header
from .store import Store

TEXT_EXT = {".txt", ".md", ".markdown"}
DOCLING_EXT = {".pdf", ".docx", ".pptx", ".html", ".htm"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp"}
MAIL_EXT = {".eml", ".mbox"}  # read as text, one section per message
SUPPORTED = TEXT_EXT | DOCLING_EXT | IMAGE_EXT | MAIL_EXT

FIGURE_PROMPT = (
    "You are indexing a figure from a document so it can be searched. "
    "First transcribe every visible word, number and label exactly. "
    "If it is a chart or table, then list every data point as 'label: value' with units. "
    "End with one sentence saying what the figure shows. Plain text, no preamble."
)

# Text that talks to an AI instead of the human reader is a prompt-injection attempt, not document content.
_INJECTION = re.compile(
    r"ignore (all |any |your |the )?(previous|prior|above|earlier|system) (instructions|prompts?|rules)"
    r"|disregard (all |any |your |the )?(previous|prior|above) "
    r"|(note|message|instructions?) (to|for) (ai|llms?|language models?|assistants?|chatbots?|ai systems?)"
    r"|\b(system prompt|developer message)\b"
    r"|\byou are (an? )?(ai|assistant|language model|chatbot)\b"
    r"|\b(ai|llm|assistant|chatbot)s? (reading|processing|summari[sz]ing) this",
    re.IGNORECASE,
)

# A numbered label on its own line ("Article 17", "Section 4.2", "Clause 9") that the next heading names.
_LABEL = re.compile(r"^(article|section|chapter|part|clause|annex|schedule|title)\s+[\w.()-]{1,12}$", re.IGNORECASE)
_TOP_LABEL = re.compile(r"^(chapter|part|annex|schedule|title|book|volume)\b", re.IGNORECASE)
_NUMBERED = re.compile(r"^\d{1,3}(\.\d{1,3})*\.?\s+\S")


def looks_like_injection(text: str) -> bool:
    return bool(_INJECTION.search(text))


@dataclass
class Chunk:
    section: str
    page_start: int
    page_end: int
    kind: str
    text: str


@dataclass
class Segment:
    chunks: list
    done: int          # units (pages or lines) fully processed after this segment
    figures: int = 0


# headings ------------------------------------------------------------------------
class Headings:
    """Tracks a two-level heading path. Level 1: ALL-CAPS titles, Part/Chapter/Annex labels.
    Level 2: numbered clauses ("4. Payment") and Article/Section labels joined to the title line after them."""

    def __init__(self):
        self.l1 = ""
        self.l2 = ""
        self.pending_label = None
        self.last_l1_line = ""  # the line that set l1, for headings that wrap onto a second line

    @property
    def path(self) -> str:
        return " › ".join(p for p in (self.l1, self.l2) if p)

    def classify(self, line: str) -> int:
        s = line.strip()
        if not s or len(s) > 120 or s[-1] in ".,;:" and not _LABEL.match(s):
            return 0
        if len(s.split()) >= 2 and self._caps(s, min_letters=4):
            return 1
        if _LABEL.match(s):
            return 1 if _TOP_LABEL.match(s) else 2
        if _NUMBERED.match(s) and len(s) <= 80:
            return 2
        return 0

    @staticmethod
    def _caps(s: str, min_letters: int = 2) -> bool:
        letters = [c for c in s if c.isalpha()]
        return len(letters) >= min_letters and sum(c.isupper() for c in letters) / len(letters) > 0.85

    def feed(self, line: str) -> bool:
        """Returns True if the line was a heading (and updates the path)."""
        s = " ".join(line.split())
        prev, self.last_l1_line = self.last_l1_line, ""
        words = len(s.split())
        if prev and self._caps(s) and ((len(prev) >= 55 and words <= 6) or words <= 3):
            self.l1 = f"{self.l1} {s}"  # "…AND NORTHWIND LOGISTICS" + "AS": one heading that wrapped
            self.last_l1_line = s
            return True
        if self.pending_label:  # "Article 17" + "Right to erasure" -> "Article 17 — Right to erasure"
            label, self.pending_label = self.pending_label, None
            if s and len(s) <= 100 and s[-1] not in ".,;" and not self.classify(s):
                self.l2 = f"{label} — {s}"
                return True
        level = self.classify(s)
        if not level:
            return False
        if level == 1:
            self.l1, self.l2, self.last_l1_line = s, "", s
        else:
            self.l2 = s
            if _LABEL.match(s):
                self.pending_label = s
        return True


# streaming ingest ----------------------------------------------------------------
def file_digest(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def ingest_file(path: str | Path, store: Store, display_name: str | None = None, progress=lambda msg: None,
                resume: bool = True) -> dict:
    path = Path(path)
    ext = path.suffix.lower()
    if ext not in SUPPORTED:
        raise ValueError(f"Unsupported file type: {ext}")
    name = display_name or path.name
    digest = file_digest(path)
    doc_id = digest[:16]
    versions = {"embed_model": config.EMBED_MODEL, "chunker_version": config.CHUNKER_VERSION}

    existing = store.document(doc_id)
    same_build = existing and all(existing.get(k) == v for k, v in versions.items())
    if same_build and existing.get("status", "ready") == "ready":
        progress("already indexed")
        return existing
    start = existing.get("checkpoint", 0) if same_build and resume and existing.get("status") == "indexing" else 0
    if start or existing:
        store.delete_rows_after(doc_id, start)  # start 0 clears the old passages but keeps the stored file

    stored = store.files_dir / f"{doc_id}{ext}"
    if not stored.exists() or stored.stat().st_size != path.stat().st_size:
        shutil.copyfile(path, stored)

    reader, total, unit = _reader(stored, ext)
    meta = {"id": doc_id, "name": name, "ext": ext, "stored_path": str(stored), "sha256": digest, "unit": unit,
            "pages": total, "status": "indexing", "checkpoint": start, **versions}
    if not start:
        meta |= {"chunks": 0, "figures": 0, "quarantined": 0, "added_at": time.time()}
    store.register(meta)

    t0, done_at_start = time.time(), start
    next_fts = [f for f in (0.25, 0.5, 0.75) if f * total > start]
    for seg in reader(start, progress):
        rows = _embed_rows(seg.chunks, doc_id, name)
        doc = store.document(doc_id)
        store.add_rows(rows)
        rate = (seg.done - done_at_start) / max(time.time() - t0, 1e-6)
        eta = (total - seg.done) / rate if rate > 0 else None
        store.update(doc_id, checkpoint=seg.done, chunks=doc["chunks"] + len(rows),
                     figures=doc["figures"] + seg.figures,
                     quarantined=doc["quarantined"] + sum(r["kind"] == "quarantined" for r in rows),
                     eta_s=round(eta) if eta is not None else None)
        if next_fts and seg.done >= next_fts[0] * total:
            next_fts.pop(0)
            store.refresh_fts()  # keyword search covers what is indexed so far
        progress(f"{unit} {seg.done:,} of {total:,} · {doc['chunks'] + len(rows):,} passages"
                 + (f" · {_fmt_eta(eta)} left" if eta and seg.done < total else ""))
    progress("finishing the index")
    store.finalize(doc_id)
    progress("done")
    return store.document(doc_id)


def _fmt_eta(seconds: float) -> str:
    return f"{seconds / 60:.0f} min" if seconds >= 90 else f"{seconds:.0f} s"


def _embed_rows(chunks: list[Chunk], doc_id: str, name: str) -> list[dict]:
    for c in chunks:
        c.text = lang.fix_devanagari(c.text)
        if c.kind != "figure" and looks_like_injection(c.text):
            c.kind = "quarantined"  # stored and visible, never shown to the model as a source
    vectors = llm.embed([passage_with_header({"doc_name": name, "section": c.section, "text": c.text})
                         for c in chunks]) if chunks else []
    return [{"id": uuid.uuid4().hex, "doc_id": doc_id, "doc_name": name, "section": c.section,
             "page_start": c.page_start, "page_end": c.page_end, "kind": c.kind, "text": c.text, "vector": v}
            for c, v in zip(chunks, vectors)]


def _reader(path: Path, ext: str):
    """Returns (segment generator, total units, unit name)."""
    if ext in MAIL_EXT:
        from .mail import to_markdown  # mail imports the harness; keep it out of import time
        text_path = path.with_suffix(".mail.md")
        if not text_path.exists():
            text_path.write_text(to_markdown(path), encoding="utf-8")
        path, ext = text_path, ".md"
    if ext in TEXT_EXT:
        with open(path, "rb") as f:
            total = sum(block.count(b"\n") for block in iter(lambda: f.read(1 << 20), b"")) + 1
        return (lambda start, progress: read_text(path, start)), total, "line"
    if ext in IMAGE_EXT:
        return (lambda start, progress: iter([Segment(chunk_image(path), 1, 1)] if start < 1 else [])), 1, "page"
    if ext == ".pdf":
        import pypdfium2 as pdfium
        from .pdflock import PDFIUM
        with PDFIUM:
            pdf = pdfium.PdfDocument(str(path))
            n = len(pdf)
            sample = [pdf[i].get_textpage().get_text_range() for i in sorted({int(k * (n - 1) / 9) for k in range(10)} if n > 1 else {0})]
            pdf.close()
        if (sum(len(t.strip()) < config.SCANNED_PAGE_CHARS for t in sample) >= 0.6 * len(sample)
                or lang.looks_like_kruti_dev(" ".join(sample))):
            # a scan: every page is read by OCR that knows Indian scripts (Docling's OCR reads Hindi as gibberish)
            return (lambda start, progress: read_scanned_pdf(path, start, progress, n)), n, "page"
        if n > config.DOCLING_MAX_PAGES and lang.devanagari_share(" ".join(sample)) < 0.3:
            return (lambda start, progress: read_pdf_text_layer(path, start, progress)), n, "page"
        # Hindi PDFs always go through Docling: the fast reader's text layer scrambles Devanagari
        return (lambda start, progress: _docling_segment(path, start, progress, n)), n, "page"
    return (lambda start, progress: _docling_segment(path, start, progress, 1)), 1, "page"


def _docling_segment(path, start, progress, total):
    if start >= total:
        return
    progress("reading layout, tables and figures")
    chunks, figures = chunk_with_docling(path, progress)
    yield Segment(chunks, total, figures)


# text and markdown ------------------------------------------------------------------
def read_text(path: Path, start_line: int = 0, segment_lines: int = config.TEXT_SEGMENT_LINES):
    """Streams paragraphs; page_start/page_end hold line numbers. Segments end on paragraph breaks."""
    heads = Headings()
    builder = _Builder()
    para, para_line, seg_start, lineno = [], 0, start_line, start_line
    with open(path, encoding="utf-8", errors="replace") as f:
        for lineno, raw in enumerate(f, 1):
            line = raw.rstrip("\n")
            if lineno <= start_line:
                m = re.match(r"^(#{1,6})\s+(.*)", line)  # replay headings so the path is right after a resume
                if m:
                    _md_heading(heads, len(m.group(1)), m.group(2))
                continue
            m = re.match(r"^(#{1,6})\s+(.*)", line)
            if m or not line.strip():
                if para:
                    builder.add(heads.path, para_line, " ".join(para), lineno - 1)
                    para = []
                if m:
                    builder.flush()
                    _md_heading(heads, len(m.group(1)), m.group(2))
                if lineno - seg_start >= segment_lines and not para:
                    builder.flush()
                    yield Segment(builder.take(), lineno)
                    seg_start = lineno
                continue
            if not para:
                para_line = lineno
            para.append(line.strip())
    if para:
        builder.add(heads.path, para_line, " ".join(para), lineno)
    builder.flush()
    yield Segment(builder.take(), lineno)


def _md_heading(heads: Headings, level: int, text: str):
    text = text.strip()
    if level == 1:
        heads.l1, heads.l2 = text, ""
    else:
        heads.l2 = text


# large PDFs: the text layer ---------------------------------------------------------
def _norm(line: str) -> str:
    return re.sub(r"\d+", "#", " ".join(line.split()).lower())


def learn_page_furniture(pages_text: list[str]) -> set[str]:
    """Lines (digits normalised) that repeat at the top or bottom of many pages: running headers and footers."""
    if len(pages_text) < 8:
        return set()
    counts = Counter()
    for text in pages_text:
        lines = [l for l in text.splitlines() if l.strip()]
        counts.update({_norm(l) for l in lines[:3] + lines[-3:]})
    return {line for line, c in counts.items() if c >= 0.4 * len(pages_text) and line}


def read_pdf_text_layer(path: Path, start_page: int = 0, progress=lambda m: None):
    import pypdfium2 as pdfium
    from .pdflock import PDFIUM

    def page_text(i):
        with PDFIUM:
            return pdf[i].get_textpage().get_text_range()
    with PDFIUM:
        pdf = pdfium.PdfDocument(str(path))
        n = len(pdf)
    sample = sorted({int(i * n / min(n, 200)) for i in range(min(n, 200))})
    furniture = learn_page_furniture([page_text(i) for i in sample])
    heads = Headings()
    if start_page:  # replay headings up to the checkpoint so section paths stay right after a resume
        for i in range(start_page):
            for line in page_text(i).splitlines():
                if _norm(line) not in furniture:
                    heads.feed(line)
    builder = _Builder()
    step = config.PDF_SEGMENT_PAGES
    for seg_start in range(start_page, n, step):
        scanned = []
        for i in range(seg_start, min(seg_start + step, n)):
            page_no = i + 1
            text = page_text(i)
            lines = [l for l in text.splitlines() if l.strip() and _norm(l) not in furniture]
            if sum(len(l) for l in lines) < config.SCANNED_PAGE_CHARS:
                scanned.append(page_no)
                continue
            body = []
            for line in lines:
                before = heads.path  # text read before a heading belongs to the section open at the time
                if heads.feed(line):
                    if body:
                        builder.add(before, page_no, _join(body))
                        body = []
                    builder.flush()
                else:
                    body.append(line)
            if body:
                builder.add(heads.path, page_no, _join(body))
        builder.flush()
        chunks = builder.take()
        for page_no in scanned:
            chunks += _ocr_page(path, page_no, heads.path)
        yield Segment(chunks, min(seg_start + step, n))


def _join(lines: list[str]) -> str:
    out = ""
    for line in lines:
        line = line.strip()
        if out.endswith("-") and line[:1].islower():
            out = out[:-1] + line  # re-join words hyphenated across lines
        else:
            out = f"{out} {line}" if out else line
    return out


def _ocr_page(path: Path, page_no: int, section: str) -> list[Chunk]:
    """A page with no text layer: render it and read it with OCR that knows Indian scripts."""
    import pypdfium2 as pdfium
    from . import ocr
    from .pdflock import PDFIUM
    try:
        with PDFIUM:
            pdf = pdfium.PdfDocument(str(path))
            img = pdf[page_no - 1].render(scale=2.0).to_pil()
            pdf.close()
        text, score, _ = ocr.read_image(img)
    except Exception:
        return []
    if score < 0.5:
        return []
    return [Chunk(section, page_no, page_no, "text", piece) for piece in _split_long(text.replace("\n", " ")) if piece.strip()]


def read_scanned_pdf(path: Path, start: int, progress, total: int, segment: int = 10):
    """A scanned PDF: OCR page by page, a checkpoint every few pages."""
    chunks = []
    for page_no in range(start + 1, total + 1):
        progress(f"reading page {page_no} of {total} with OCR")
        chunks += _ocr_page(path, page_no, f"Page {page_no}")
        if page_no % segment == 0 or page_no == total:
            yield Segment(chunks, page_no)
            chunks = []


# images -----------------------------------------------------------------------------
def chunk_image(path: Path) -> list[Chunk]:
    from PIL import Image
    img = Image.open(path).convert("RGB")
    img.thumbnail((1600, 1600))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    desc = llm.describe_image(buf.getvalue(), FIGURE_PROMPT)
    out = [Chunk("Image", 1, 1, "figure", piece) for piece in _split_long(desc)]
    # a photo or scan of a letter, form or notice: its text read by OCR (Hindi included), next to the description
    from . import ocr
    try:
        text, score, _ = ocr.read_image(img)
    except Exception:
        text, score = "", 0.0
    if score >= 0.6 and len(text) >= 40:
        out += [Chunk("Image text", 1, 1, "text", piece) for piece in _split_long(text.replace("\n", " "))]
    return out


# small PDF / DOCX / PPTX / HTML via Docling -----------------------------------------
@lru_cache(maxsize=2)
def _converter(ocr: bool):
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption
    opts = PdfPipelineOptions()
    if config.DOCLING_MODELS.exists():
        opts.artifacts_path = str(config.DOCLING_MODELS)
    opts.do_ocr = ocr
    opts.do_table_structure = True
    opts.generate_picture_images = True
    opts.images_scale = 2.0
    return DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})


def release_memory():
    """After ingest: unload the vision model and Docling's parsers so chat has room on 16 GB machines."""
    import gc
    llm.unload(config.VISION_MODEL)
    _converter.cache_clear()
    gc.collect()
    try:
        import torch
        if torch.backends.mps.is_available():
            torch.mps.empty_cache()
    except ImportError:
        pass


def chunk_with_docling(path: Path, progress) -> tuple[list[Chunk], int]:
    from docling_core.types.doc import PictureItem, SectionHeaderItem, TableItem, TextItem, TitleItem
    try:
        doc = _converter(True).convert(str(path)).document
    except Exception:
        doc = _converter(False).convert(str(path)).document  # OCR engine unavailable: text layer only

    builder = _Builder()
    section = ""
    label = None  # "Article 17" waiting for its title line
    figures = []
    for item, _level in doc.iterate_items():
        page = item.prov[0].page_no if getattr(item, "prov", None) else 1
        text = getattr(item, "text", "").strip()
        if isinstance(item, (TextItem, TitleItem, SectionHeaderItem)) and _LABEL.match(text):
            builder.flush()
            label = section = text
            continue
        if isinstance(item, (TitleItem, SectionHeaderItem)):
            builder.flush()
            section = f"{label} — {text}" if label else text
            label = None
        elif isinstance(item, TableItem):
            builder.flush()
            table_md = item.export_to_markdown(doc=doc)
            caption = item.caption_text(doc) if hasattr(item, "caption_text") else ""
            for piece in _split_long(f"{caption}\n{table_md}".strip()):
                builder.chunks.append(Chunk(section, page, page, "table", piece))
        elif isinstance(item, PictureItem):
            figures.append((item, page, section))
        elif isinstance(item, TextItem) and text:
            label = None
            builder.add(section, page, text)
    builder.flush()

    described = 0
    if config.DESCRIBE_FIGURES:
        for i, (item, page, sect) in enumerate(figures, 1):
            img = item.get_image(doc)
            if img is None or min(img.size) < config.MIN_FIGURE_PX:
                continue
            progress(f"describing figure {i}/{len(figures)}")
            buf = io.BytesIO()
            img.convert("RGB").save(buf, format="PNG")
            caption = item.caption_text(doc) if hasattr(item, "caption_text") else ""
            desc = llm.describe_image(buf.getvalue(), FIGURE_PROMPT)
            described += 1
            for piece in _split_long(f"Figure: {caption}\n{desc}".strip()):
                builder.chunks.append(Chunk(sect, page, page, "figure", piece))
    return builder.chunks, described


# kept for callers and tests that chunk a whole string at once
def chunk_plain_text(text: str) -> list[Chunk]:
    builder = _Builder()
    section = ""
    for block in re.split(r"\n\s*\n", text):
        block = block.strip()
        if not block:
            continue
        heading = re.match(r"^(#{1,6})\s+(.*)", block)
        if heading and "\n" not in block:
            section = heading.group(2).strip()
            builder.flush()
            continue
        builder.add(section, 1, block)
    builder.flush()
    return builder.chunks


# chunk assembly ---------------------------------------------------------------------
class _Builder:
    """Packs paragraphs into ~CHUNK_CHARS chunks that never cross a section boundary."""

    def __init__(self):
        self.chunks: list[Chunk] = []
        self.buf: list[str] = []
        self.section = ""
        self.first_page = self.last_page = 1

    def add(self, section, page, text, last_page=None):
        if self.buf and (section != self.section or self._size() + len(text) > config.CHUNK_CHARS):
            self.flush(carry=section == self.section)
        if not self.buf:
            self.section, self.first_page = section, page
        for piece in _split_long(text):
            if self.buf and self._size() + len(piece) > config.CHUNK_CHARS:
                self.flush(carry=True)
                self.first_page = page
            self.buf.append(piece)
        self.last_page = last_page or page

    def _size(self):
        return sum(len(b) + 1 for b in self.buf)

    def flush(self, carry=False):
        if not self.buf:
            return
        self.chunks.append(Chunk(self.section, self.first_page, self.last_page, "text", "\n".join(self.buf)))
        tail = self.buf[-1][-config.CHUNK_OVERLAP_CHARS:] if carry else ""
        self.buf = [tail] if carry and tail else []
        self.first_page = self.last_page

    def take(self) -> list[Chunk]:
        out, self.chunks = self.chunks, []
        return out


def _split_long(text: str) -> list[str]:
    """Split text longer than CHUNK_CHARS at sentence boundaries."""
    if len(text) <= config.CHUNK_CHARS:
        return [text]
    out, cur = [], ""
    for sent in re.split(r"(?<=[.!?])\s+|\n", text):
        if cur and len(cur) + len(sent) + 1 > config.CHUNK_CHARS:
            out.append(cur)
            cur = ""
        cur = f"{cur} {sent}".strip() if cur else sent
        while len(cur) > config.CHUNK_CHARS:
            out.append(cur[:config.CHUNK_CHARS])
            cur = cur[config.CHUNK_CHARS:]
    if cur:
        out.append(cur)
    return out
