"""End-to-end check of projects, folder sync, chats, slash commands, thinking and attachments.
Usage: python evals/e2e_workspace.py --url http://127.0.0.1:8765 --token T --folder /tmp/some-empty-dir"""
import argparse
import json
import shutil
import sys
import time
from pathlib import Path

import httpx

DOCS = Path(__file__).parent / "docs"
results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""), flush=True)


def send(c, chat_id, text, think=False, mode="accurate"):
    """Posts a message and returns (event types seen, final result, thinking text, seconds)."""
    t, types, result, thinking = time.time(), [], None, []
    with c.stream("POST", f"/chats/{chat_id}/messages", json={"text": text, "think": think, "mode": mode}) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if not line.startswith("data:"):
                continue
            ev = json.loads(line[5:])
            types.append(ev["type"])
            if ev["type"] == "thinking":
                thinking.append(ev["text"])
            if ev["type"] == "result":
                result = ev["result"]
    return types, result, "".join(thinking), round(time.time() - t, 1)


def wait_job(c, job_id, limit=600):
    for _ in range(limit):
        job = c.get(f"/jobs/{job_id}").json()
        if job["status"] in ("done", "error"):
            return job
        time.sleep(1)
    return job


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--token", required=True)
    ap.add_argument("--folder", required=True)
    a = ap.parse_args()
    c = httpx.Client(base_url=a.url, headers={"x-vyum-token": a.token}, timeout=900)
    folder = Path(a.folder)
    shutil.rmtree(folder, ignore_errors=True)
    (folder / "contracts").mkdir(parents=True)
    shutil.copy(DOCS / "acme_supply_agreement.pdf", folder / "contracts")
    shutil.copy(DOCS / "globex_supply_agreement.docx", folder / "contracts")
    shutil.copy(DOCS / "employee_handbook.md", folder)
    (folder / "node_modules").mkdir()
    (folder / "node_modules" / "ignored.md").write_text("# Ignored\n\nThis must never be indexed.")
    (folder / ".hidden.md").write_text("# Hidden\n\nThis must never be indexed.")

    check("engine healthy", c.get("/health").json()["ok"])

    # project + scan
    p = c.post("/projects", json={"folder": str(folder), "name": "Supplier contracts"}).json()
    job = wait_job(c, p["scan"]["id"])
    p = c.get(f"/projects/{p['id']}").json()
    paths = sorted(f["path"] for f in p["files"])
    check("project scan indexes the folder", job["status"] == "done" and paths == [
        "contracts/acme_supply_agreement.pdf", "contracts/globex_supply_agreement.docx", "employee_handbook.md"],
        f"{paths} {job.get('result')}")

    # chat inside the project, with a follow-up
    chat = c.post("/chats", json={"project_id": p["id"]}).json()
    types, r, _, s = send(c, chat["id"], "What are the payment terms in the Acme agreement?")
    check("answer streams status and draft events", "status" in types and "draft" in types, f"{s}s")
    check("project chat answers from the folder", r["kind"] == "answer" and "45" in r["answer"], r["answer"][:90])
    types, r, _, s = send(c, chat["id"], "And what are they in the Globex one?")
    check("follow-up question is resolved from the conversation",
          r.get("status") == "answered" and ("30" in r["answer"] or "thirty" in r["answer"].lower()),
          f"asked as: {r.get('resolved_question')!r} -> {r['answer'][:80]}")
    check("chat title set from first message", c.get(f"/chats/{chat['id']}").json()["title"].startswith("What are"))

    # slash commands
    _, r, _, _ = send(c, chat["id"], "/help")
    check("/help lists commands", r["kind"] == "help" and len(r["commands"]) >= 6)
    _, r, _, _ = send(c, chat["id"], "/files")
    check("/files lists the project files", r["kind"] == "files" and len(r["files"]) == 3)
    _, r, _, _ = send(c, chat["id"], "/compare")
    check("/compare alone shows instructions", r["kind"] == "instructions" and r["usage"].startswith("/compare")
          and len(r["files"]) == 3, r.get("note"))
    _, r, _, s = send(c, chat["id"], "/compare acme globex payment terms and warranty")
    cells = [cell["text"] for row in r.get("rows", []) for cell in row["cells"].values()]
    check("/compare builds a cited table", r["kind"] == "compare" and len(r.get("documents", [])) == 2
          and any("45" in x for x in cells) and any("30" in x or "thirty" in x.lower() for x in cells),
          f"{len(r.get('rows', []))} rows, {s}s")
    _, r, _, _ = send(c, chat["id"], "/find liability")
    check("/find returns keyword hits with pages", r["kind"] == "find" and len(r["hits"]) >= 2
          and all("page" in h for h in r["hits"]), f"{len(r.get('hits', []))} hits")
    _, r, _, s = send(c, chat["id"], "/summarize acme")
    check("/summarize gives cited bullet points", r["kind"] == "answer" and r["status"] == "answered"
          and len(r["sentences"]) >= 3 and all(x["citations"] for x in r["sentences"] if x["text"].startswith("-")),
          f"{len(r.get('sentences', []))} points, {s}s")
    _, r, _, _ = send(c, chat["id"], "/nonsense")
    check("unknown command explains itself", r["kind"] == "instructions" and "no /nonsense" in r["note"])

    # thinking
    chat2 = c.post("/chats", json={"project_id": p["id"]}).json()
    types, r, thinking, s = send(c, chat2["id"], "Which supply agreement has the longer warranty, and by how many months?",
                                 think=True)
    check("thinking streams before the answer", "thinking" in types and "thinking_done" in types
          and types.index("thinking") < types.index("draft"), f"{len(thinking)} chars of reasoning, {s}s")
    check("thinking answer is correct", r.get("status") == "answered" and "acme" in r["answer"].lower()
          and ("6" in r["answer"] or "six" in r["answer"].lower()), r["answer"][:120])

    # folder sync: one new, one changed, one deleted
    time.sleep(1.1)  # make sure mtimes differ
    (folder / "travel_policy.md").write_text("# Travel policy\n\nEconomy class is required for flights under 6 hours.")
    with open(folder / "employee_handbook.md", "a") as f:
        f.write("\n\n# Parental leave\n\nEmployees receive 26 weeks of paid parental leave.\n")
    (folder / "contracts" / "globex_supply_agreement.docx").unlink()
    _, r, _, _ = send(c, chat["id"], "/rescan")
    job = wait_job(c, r["job"]["id"])
    check("/rescan picks up added, changed and deleted files",
          job["status"] == "done" and job["result"]["added"] == 1 and job["result"]["changed"] == 1
          and job["result"]["removed"] == 1, json.dumps(job.get("result")))
    _, r, _, _ = send(c, chat["id"], "How many weeks of paid parental leave do employees get?")
    check("changed file is searchable", r.get("status") == "answered" and "26" in r["answer"], r["answer"][:80])
    _, r, _, _ = send(c, chat["id"], "What is the liability cap in the Globex agreement?")
    check("deleted file is gone", r.get("status") == "not_found" or "500,000" not in r["answer"], r["answer"][:80])

    # plain chat with attachments
    chat3 = c.post("/chats", json={}).json()
    files = [("files", (n, (DOCS / n).read_bytes())) for n in ("q3_revenue_by_region.png", "employee_handbook.md")]
    jobs = c.post(f"/chats/{chat3['id']}/files", files=files).json()
    done = [wait_job(c, j["id"]) for j in jobs]
    check("multiple files attach to a chat", all(j["status"] == "done" for j in done)
          and len(c.get(f"/chats/{chat3['id']}").json()["doc_ids"]) == 2)
    _, r, _, _ = send(c, chat3["id"], "Which region had the highest Q3 2026 revenue?")
    check("attached image answers through the vision model", r.get("status") == "answered" and "West" in r["answer"])

    listed = c.get("/chats").json()
    check("plain chats and project chats are listed apart", any(x["id"] == chat3["id"] for x in listed)
          and not any(x["id"] == chat["id"] for x in listed))

    c.delete(f"/chats/{chat3['id']}")
    c.delete(f"/projects/{p['id']}")
    check("project delete removes its chats", c.get(f"/chats/{chat['id']}").status_code == 404)

    print(f"\n{sum(results)}/{len(results)} checks passed")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
