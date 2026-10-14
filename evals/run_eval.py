"""End-to-end eval through the HTTP API. Usage: python evals/run_eval.py --url http://127.0.0.1:PORT --token T [--mode accurate] [--ingest]"""
import argparse
import json
import sys
import time
from pathlib import Path

import httpx

HERE = Path(__file__).parent


def _in_language(text: str, code: str) -> bool:
    """Hindi answers are mostly Devanagari; Hinglish and English answers are mostly Latin letters."""
    import re
    letters = [c for c in re.sub(r"\[\d+\]", "", text) if c.isalpha()]
    deva = sum("\u0900" <= c <= "\u097f" for c in letters)
    share = deva / max(len(letters), 1)
    if code == "hinglish":  # Latin letters and real Hindi words, not an English answer
        words = set(re.findall(r"[a-z]+", text.lower()))
        return share < 0.2 and len(words & {"hai", "hain", "mein", "ka", "ki", "ke", "ko", "se", "nahi", "aur", "kar", "hota", "liye", "chahiye",
                                    "din", "pehle", "tak", "hoti", "karna", "milta", "milti", "kitna", "wala"}) >= 2
    return share >= 0.4 if code in ("hi", "mr") else share < 0.2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--token", required=True)
    ap.add_argument("--mode", default="accurate")
    ap.add_argument("--ingest", action="store_true")
    ap.add_argument("--out", default=str(HERE / "results.json"))
    ap.add_argument("--questions", default=str(HERE / "questions.json"))
    ap.add_argument("--docs", default=str(HERE / "docs"))
    ap.add_argument("--think", action="store_true", help="answer with the model's reasoning pass on")
    a = ap.parse_args()
    c = httpx.Client(base_url=a.url, headers={"x-vyum-token": a.token}, timeout=900)

    if a.ingest:
        for f in sorted(Path(a.docs).iterdir()):
            job = c.post("/ingest", files={"file": (f.name, f.read_bytes())}).raise_for_status().json()
            t = time.time()
            while job["status"] in ("queued", "running"):
                time.sleep(2)
                job = c.get(f"/jobs/{job['id']}").json()
            d = job.get("document", {})
            print(f"ingest {f.name:32} {job['status']:5} {time.time() - t:6.1f}s chunks={d.get('chunks')} "
                  f"figures={d.get('figures')} {job.get('error', '')}")
            if job["status"] != "done":
                sys.exit(1)

    docs = c.get("/documents").json()
    results = []
    for item in json.loads(Path(a.questions).read_text()):
        r = c.post("/chat", json={"question": item["q"], "mode": a.mode, "think": a.think}).json()
        answered = r["status"] == "answered"
        text = r["answer"]
        if item.get("unanswerable"):
            verdict = "correct_abstain" if not answered else "hallucination"
            if verdict == "correct_abstain" and item.get("lang") and not _in_language(text, item["lang"]):
                verdict = "wrong_language"
        elif not answered:
            verdict = "abstained"
        else:
            ok = all(any(v.lower() in text.lower() for v in group) for group in item["expect"])
            ok = ok and not any(f.lower() in text.lower() for f in item.get("forbid", []))
            verdict = "correct" if ok else "wrong"
            if ok and item.get("lang") and not _in_language(text, item["lang"]):
                verdict = "wrong_language"
        cite_ok = None
        if answered and item.get("doc"):
            cited = {r["citations"][n - 1]["doc_name"] for s in r["sentences"] for n in s["citations"]}
            pages = {r["citations"][n - 1]["page"] for s in r["sentences"] for n in s["citations"]}
            want = item.get("page")
            want = [want] if isinstance(want, int) else want
            cite_ok = any(item["doc"] in name for name in cited) and (not want or bool(set(want) & pages))
        results.append({**item, "verdict": verdict, "citation_ok": cite_ok, "answer": text,
                        "confidence": r.get("confidence"), "best_score": r.get("best_score"),
                        "dropped": r.get("dropped"), "seconds": r["timings"].get("total_s")})
        print(f"{verdict:16} cite={str(cite_ok):5} {r['timings'].get('total_s', 0):5.1f}s  {item['q'][:60]}\n"
              f"{'':16} -> {text[:160]}")

    answerable = [x for x in results if not x.get("unanswerable")]
    answered = [x for x in answerable if x["verdict"] in ("correct", "wrong")]
    unans = [x for x in results if x.get("unanswerable")]
    cites = [x for x in answered if x["citation_ok"] is not None]
    summary = {
        "mode": a.mode, "think": a.think, "documents": len(docs),
        "faithfulness": round(sum(x["verdict"] == "correct" for x in answered) / max(len(answered), 1), 3),
        "coverage": round(len(answered) / max(len(answerable), 1), 3),
        "abstain_accuracy": round(sum(x["verdict"] == "correct_abstain" for x in unans) / max(len(unans), 1), 3),
        "citation_accuracy": round(sum(bool(x["citation_ok"]) for x in cites) / max(len(cites), 1), 3),
        "median_seconds": sorted(x["seconds"] or 0 for x in results)[len(results) // 2],
    }
    print(json.dumps(summary, indent=2))
    Path(a.out).write_text(json.dumps({"summary": summary, "results": results}, indent=2))


if __name__ == "__main__":
    main()
