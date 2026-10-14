"""LanceDB chunk table (vectors + full-text index) and a JSON registry of documents."""
import json
import re
import threading
import time
from datetime import timedelta
from pathlib import Path

import lancedb
import pyarrow as pa

from . import config

_lock = threading.RLock()

SCHEMA = pa.schema([
    pa.field("id", pa.string()),
    pa.field("doc_id", pa.string()),
    pa.field("doc_name", pa.string()),
    pa.field("section", pa.string()),
    pa.field("page_start", pa.int32()),
    pa.field("page_end", pa.int32()),
    pa.field("kind", pa.string()),  # text | table | figure
    pa.field("text", pa.string()),
    pa.field("fts", pa.string()),  # section heading + text: keyword search must see "No. 05012" in the heading
    pa.field("vector", pa.list_(pa.float32(), config.EMBED_DIM)),
])


class Store:
    def __init__(self, data_dir: Path | None = None):
        self.dir = Path(data_dir or config.DATA_DIR)
        self.files_dir = self.dir / "files"
        self.files_dir.mkdir(parents=True, exist_ok=True)
        self.registry_path = self.dir / "documents.json"
        self.db = lancedb.connect(str(self.dir / "index"))
        if "chunks" in self.db.table_names():
            self.table = self.db.open_table("chunks")
            if "fts" not in self.table.schema.names:
                self._migrate()
        else:
            self.table = self.db.create_table("chunks", schema=SCHEMA)
        self._fts_ready = self.table.count_rows() > 0

    def _migrate(self):
        """Index built by an older version: rebuild it. Documents are marked for re-indexing and the engine
        resumes them at startup from the copies kept in files/."""
        self.db.drop_table("chunks")
        self.table = self.db.create_table("chunks", schema=SCHEMA)
        docs = self.documents()
        for d in docs:
            d.update(status="indexing", checkpoint=0, chunks=0, figures=0, quarantined=0, chunker_version=None)
        self._save(docs)

    # documents ---------------------------------------------------------
    def documents(self) -> list[dict]:
        with _lock:
            if not self.registry_path.exists():
                return []
            return json.loads(self.registry_path.read_text())

    def document(self, doc_id: str) -> dict | None:
        return next((d for d in self.documents() if d["id"] == doc_id), None)

    def _save(self, docs):
        tmp = self.registry_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(docs, indent=2))
        tmp.replace(self.registry_path)

    def register(self, meta: dict):
        """Creates or replaces a document's registry entry (status "indexing" until finalize)."""
        with _lock:
            docs = [d for d in self.documents() if d["id"] != meta["id"]]
            old = next((d for d in self.documents() if d["id"] == meta["id"]), {})
            docs.append({**old, **meta})
            self._save(docs)

    def update(self, doc_id: str, **fields):
        with _lock:
            docs = self.documents()
            for d in docs:
                if d["id"] == doc_id:
                    d.update(fields)
            self._save(docs)

    def add_rows(self, rows: list[dict]):
        if rows:
            for r in rows:
                r["fts"] = f"{r['section']}\n{r['text']}"
            with _lock:
                self.table.add(rows)

    def delete_rows_after(self, doc_id: str, unit: int):
        """Resume safety: drop anything written past the last checkpoint."""
        with _lock:
            self.table.delete(f"doc_id = '{_q(doc_id)}' AND page_start > {int(unit)}")

    def refresh_fts(self):
        with _lock:
            if self.table.count_rows() > 0:
                self.table.create_fts_index("fts", replace=True)
                self._fts_ready = True

    def finalize(self, doc_id: str):
        """End of an ingest: keyword index over everything, compact the small files each batch left behind."""
        with _lock:
            self.refresh_fts()
            try:
                self.table.optimize(cleanup_older_than=timedelta(minutes=10))  # searches may still read recent versions
            except Exception:
                pass  # compaction is an optimisation; never fail an ingest over it
            n = self.table.count_rows(f"doc_id = '{_q(doc_id)}'")
            self.update(doc_id, status="ready", chunks=n, eta_s=None, indexed_at=time.time())
            self._maybe_vector_index()

    def _maybe_vector_index(self):
        """Exact search is ~14 ms at 40k passages; an approximate index only pays off around a million."""
        rows = self.table.count_rows()
        if rows >= config.VECTOR_INDEX_MIN_ROWS and not self.table.list_indices():
            self.table.create_index(metric="cosine", vector_column_name="vector", index_type="IVF_HNSW_SQ")

    def doc_rows(self, doc_id: str, columns: list[str]):
        """All passages of one document as an Arrow table, for whole-document operations."""
        where = f"doc_id = '{_q(doc_id)}'"
        n = self.table.count_rows(where)
        return self.table.search().where(where).select(columns).limit(max(n, 1)).to_arrow()

    def part_rows(self, doc_id: str, first: int, last: int):
        """Passages starting within one page (or line) range of a document."""
        where = f"doc_id = '{_q(doc_id)}' AND page_start >= {int(first)} AND page_start <= {int(last)}"
        n = self.table.count_rows(where)
        cols = ["id", "doc_id", "doc_name", "section", "page_start", "page_end", "kind", "text", "vector"]
        return self.table.search().where(where).select(cols).limit(max(n, 1)).to_arrow()

    def delete_document(self, doc_id: str):
        with _lock:
            doc = self.document(doc_id)
            self.table.delete(f"doc_id = '{_q(doc_id)}'")
            if self.table.count_rows() > 0:
                self.table.create_fts_index("fts", replace=True)
            else:
                self._fts_ready = False
            self._save([d for d in self.documents() if d["id"] != doc_id])
            if doc:
                Path(doc["stored_path"]).unlink(missing_ok=True)

    # search ------------------------------------------------------------
    @staticmethod
    def _where(doc_ids):
        clause = "kind != 'quarantined'"
        if doc_ids:
            clause += " AND doc_id IN (" + ", ".join(f"'{_q(d)}'" for d in doc_ids) + ")"
        return clause

    def vector_search(self, vec, k, doc_ids=None) -> list[dict]:
        if self.table.count_rows() == 0:
            return []
        q = self.table.search(vec, vector_column_name="vector").metric("cosine").limit(k)
        where = self._where(doc_ids)
        if where:
            q = q.where(where, prefilter=True)
        return q.to_list()

    def keyword_search(self, text, k, doc_ids=None) -> list[dict]:
        if not self._fts_ready or not text.strip():
            return []
        text = re.sub(r"[^\w\s]", " ", text)  # the query parser treats - : " ( ) as operators
        if not text.strip():
            return []
        q = self.table.search(text, query_type="fts").limit(k)
        where = self._where(doc_ids)
        if where:
            q = q.where(where, prefilter=True)
        try:
            return q.to_list()
        except Exception:
            return []  # FTS parser rejects some punctuation-only queries


def _q(s: str) -> str:
    return s.replace("'", "''")
