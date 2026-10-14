"""Hybrid retrieval: BM25 + vectors fused with reciprocal rank fusion, then a cross-encoder rerank."""
import threading
from functools import lru_cache

from . import config, llm
from .store import Store

_rerank_lock = threading.Lock()


def rrf_fuse(result_lists: list[list[dict]], k: int = config.RRF_K) -> list[dict]:
    scores, by_id = {}, {}
    for results in result_lists:
        for rank, row in enumerate(results):
            scores[row["id"]] = scores.get(row["id"], 0.0) + 1.0 / (k + rank + 1)
            by_id.setdefault(row["id"], row)
    return [by_id[i] for i in sorted(scores, key=scores.get, reverse=True)]


def hybrid_search(store: Store, queries: list[str], doc_ids=None, k=config.RETRIEVE_K) -> list[dict]:
    vectors = llm.embed(queries)
    lists = []
    for q, v in zip(queries, vectors):
        lists.append(store.vector_search(v, k, doc_ids))
        lists.append(store.keyword_search(q, k, doc_ids))
    return rrf_fuse(lists)[: k * 2]


@lru_cache(maxsize=1)
def _reranker():
    import torch
    from sentence_transformers import CrossEncoder
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    model = CrossEncoder(config.RERANK_MODEL, device=device, max_length=512)
    if device == "mps":
        model.model.half()
    return model


def passage_with_header(row: dict) -> str:
    """Document name and section heading in front of the passage. Without them a clause like
    'The Supplier warrants ... 18 months' never matches 'warranty in the Acme agreement'."""
    return f"{row['doc_name']} | {row['section']}\n{row['text']}"


def rerank(question: str, rows: list[dict], top=config.RERANK_K) -> list[dict]:
    if not rows:
        return []
    with _rerank_lock:
        scores = _reranker().predict([(question, passage_with_header(r)) for r in rows], batch_size=16,
                                     show_progress_bar=False)
    ranked = sorted(zip(rows, scores), key=lambda p: float(p[1]), reverse=True)[:top]
    return [{**r, "score": float(s)} for r, s in ranked]
