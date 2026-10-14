"""Large-document test: indexes a big PDF inside the offline sandbox, kills the engine part-way, checks it resumes
without losing or duplicating passages, then measures accuracy, whole-document commands, latency and memory.

Usage: python evals/scale_test.py --pdf evals/large/northwind_master_archive.pdf --data /tmp/vyum-scale [--kill-at 0.4]"""
import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import time
from collections import Counter
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
PY = str(ROOT / ".venv/bin/python")
report = {}


def log(msg):
    print(msg, flush=True)


class Engine:
    def __init__(self, data: Path, port: int, token: str):
        self.data, self.port, self.token, self.proc = data, port, token, None
        self.peak_mb, self._stop = 0.0, threading.Event()

    def start(self):
        env = {**os.environ, "VYUM_DATA": str(self.data), "VYUM_TOKEN": self.token}
        self.log = open(self.data.parent / f"{self.data.name}-engine.log", "a")
        self.proc = subprocess.Popen(["sandbox-exec", "-f", str(ROOT / "scripts/offline.sb"), PY, "-m", "engine.main",
                                      "--port", str(self.port)], cwd=ROOT, env=env, stdout=self.log, stderr=self.log,
                                     start_new_session=True)
        for _ in range(240):
            try:
                if httpx.get(f"http://127.0.0.1:{self.port}/health", timeout=2).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.5)
        self._stop.clear()
        threading.Thread(target=self._sample, daemon=True).start()

    def _sample(self):
        while not self._stop.is_set() and self.proc and self.proc.poll() is None:
            out = subprocess.run(["ps", "-o", "rss=", "-g", str(os.getpgid(self.proc.pid))], capture_output=True, text=True).stdout
            mb = sum(int(x) for x in out.split()) / 1024
            self.peak_mb = max(self.peak_mb, mb)
            time.sleep(1)

    def kill(self):
        self._stop.set()
        os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
        self.proc.wait()

    def stop(self):
        self._stop.set()
        os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
        self.proc.wait(timeout=30)


def send(c, chat_id, text):
    t, result = time.time(), None
    with c.stream("POST", f"/chats/{chat_id}/messages", json={"text": text, "mode": "accurate"}) as r:
        for line in r.iter_lines():
            if line.startswith("data:"):
                ev = json.loads(line[5:])
                if ev["type"] == "result":
                    result = ev["result"]
    return result, round(time.time() - t, 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--data", required=True)
    ap.add_argument("--port", type=int, default=8790)
    ap.add_argument("--kill-at", type=float, default=0.4)
    a = ap.parse_args()
    data, pdf, token = Path(a.data), Path(a.pdf), "scaletest"
    subprocess.run(["rm", "-rf", str(data)])
    data.mkdir(parents=True)
    url = f"http://127.0.0.1:{a.port}"
    c = httpx.Client(base_url=url, headers={"x-vyum-token": token}, timeout=1800)

    # what a clean run must produce, from the parser alone (no model involved)
    sys.path.insert(0, str(ROOT))
    from engine.ingest import read_pdf_text_layer
    expected = sum(len(s.chunks) for s in read_pdf_text_layer(pdf, 0))
    stats = json.loads((pdf.parent / "stats.json").read_text())
    report["document"] = {"tokens": stats["tokens"], "pages": stats["pages"], "expected_passages": expected}
    log(f"document: {stats['tokens']:,} tokens, {stats['pages']:,} pages, {expected:,} passages expected")

    # 1. index, ask a needle while indexing, crash at kill-at
    eng = Engine(data, a.port, token)
    eng.start()
    t0 = time.time()
    with open(pdf, "rb") as f:
        job = c.post("/ingest", files={"file": (pdf.name, f, "application/pdf")}).raise_for_status().json()
    first_needle = next(q for q in json.loads((ROOT / "evals/questions_large.json").read_text()) if q["type"] == "needle")
    asked_early = None
    while True:
        docs = c.get("/documents").json()
        doc = docs[0] if docs else None
        done = (doc or {}).get("checkpoint", 0)
        if doc and asked_early is None and done >= 0.15 * doc["pages"]:
            r = c.post("/chat", json={"question": first_needle["q"]}).json()
            ok = r["status"] == "answered" and all(any(v.lower() in r["answer"].lower() for v in g) for g in first_needle["expect"])
            asked_early = {"progress": round(done / doc["pages"], 2), "correct": ok, "answer": r["answer"][:160]}
            log(f"asked while indexing ({asked_early['progress']:.0%} done): {'correct' if ok else 'WRONG'} · {r['answer'][:90]}")
        if doc and done >= a.kill_at * doc["pages"]:
            break
        if doc and doc.get("status") == "ready":
            break
        time.sleep(0.5)
    killed_at = done
    report["searchable_while_indexing"] = asked_early
    eng.kill()
    rows_before = None
    log(f"killed the engine (SIGKILL) at page {killed_at:,} of {doc['pages']:,} after {time.time() - t0:.0f}s")

    # 2. restart: the engine resumes on its own from the checkpoint saved on disk
    resumed_from = json.loads((data / "documents.json").read_text())[0].get("checkpoint", 0)
    eng.start()
    log(f"restarted; resuming from page {resumed_from:,}")
    while c.get("/documents").json()[0].get("status") != "ready":
        time.sleep(1)
    ingest_s = round(time.time() - t0, 1)
    doc = c.get("/documents").json()[0]
    report["ingest"] = {"seconds_total_incl_crash": ingest_s, "killed_at_page": killed_at, "resumed_from_page": resumed_from,
                        "passages": doc["chunks"], "peak_engine_mb": round(eng.peak_mb)}
    log(f"indexed in {ingest_s}s (including the crash): {doc['chunks']:,} passages; engine peak memory {eng.peak_mb:.0f} MB")

    # 3. integrity: read the index directly
    eng.stop()
    os.environ["VYUM_DATA"] = str(data)
    from engine.store import Store
    t = Store(data).doc_rows(doc["id"], ["page_start", "text"])
    pairs = list(zip(t["page_start"].to_pylist(), t["text"].to_pylist()))
    dupes = sum(n - 1 for n in Counter(pairs).values() if n > 1)
    pages = {p for p, _ in pairs}
    report["integrity"] = {"rows": len(pairs), "expected": expected, "duplicates": dupes,
                           "first_page": min(pages), "last_page": max(pages)}
    log(f"integrity: {len(pairs):,} rows vs {expected:,} expected · {dupes} duplicates · pages {min(pages)}–{max(pages)}")
    eng.start()

    # 4. accuracy on all questions
    out = ROOT / "evals/results_large.json"
    subprocess.run([PY, "-u", str(ROOT / "evals/run_eval.py"), "--url", url, "--token", token,
                    "--questions", str(ROOT / "evals/questions_large.json"), "--mode", "accurate", "--out", str(out)], check=True)
    report["accuracy"] = json.loads(out.read_text())["summary"]

    # 5. whole-document commands
    chat = c.post("/chats", json={}).json()
    r, s = send(c, chat["id"], "/outline northwind")
    report["outline"] = {"seconds": s, "sections": r.get("sections"), "series": [(x["pattern"], x["count"]) for x in r.get("series", [])]}
    log(f"/outline: {s}s · {r.get('sections'):,} sections · series {report['outline']['series']}")
    r, s = send(c, chat["id"], "/summarize northwind")
    cited_pages = sorted({c_["page"] for c_ in r.get("citations", [])})
    report["summary"] = {"seconds": s, "coverage": r.get("coverage"), "bullets": len(r.get("sentences", [])),
                         "cited_pages": cited_pages, "dropped": len(r.get("dropped", []))}
    log(f"/summarize: {s}s · parts covered {r.get('coverage')} · {len(r.get('sentences', []))} sentences · pages cited {cited_pages[:3]}…{cited_pages[-3:]}")
    r2, s2 = send(c, chat["id"], "/summarize northwind")
    report["summary"]["cached_seconds"] = s2
    r, s = send(c, chat["id"], "/find QX-4471-B")
    report["find"] = {"seconds": s, "hits": len(r.get("hits", [])), "first": r["hits"][0]["page"] if r.get("hits") else None}
    log(f"/find QX-4471-B: {s}s · {report['find']['hits']} hits, first on page {report['find']['first']}")
    resp = c.get(f"/documents/{doc['id']}/page/300")
    report["page_viewer"] = {"status": resp.status_code, "bytes": len(resp.content), "type": resp.headers.get("content-type"),
                             "full_pdf_bytes": pdf.stat().st_size}
    log(f"single-page viewer: {resp.status_code}, {len(resp.content):,} bytes vs {pdf.stat().st_size:,} for the whole PDF")
    report["disk_mb"] = round(sum(f.stat().st_size for f in data.rglob("*") if f.is_file()) / 1e6, 1)
    eng.stop()
    (ROOT / "evals/results_scale.json").write_text(json.dumps(report, indent=1))
    log(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
