"""HTTP API used by the desktop UI. Binds to 127.0.0.1; every request except /health needs the launch token."""
import json
import re
import os
import secrets
import tempfile
import threading
import time
import uuid
from pathlib import Path

import httpx
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from . import assistant, commands, config, harness, llm, mail, mailauth, mailbox, translate, web
from .ingest import SUPPORTED, ingest_file, release_memory
from .store import Store
from .workspace import Workspace

TOKEN = os.environ.get("VYUM_TOKEN") or secrets.token_urlsafe(24)
app = FastAPI(title="Vyum engine")
store = Store()
ws = Workspace(store)
mailbox.background()
mailbox.start_sync()
translate.warm_up()
jobs: dict[str, dict] = {}
_ingest_lock = threading.Lock()  # one ingest at a time keeps memory predictable on 16 GB machines


@app.middleware("http")
async def require_token(request: Request, call_next):
    # the OAuth redirect comes from the system browser, which has no token; the one-time state code protects it
    # the PDF editor's page and its scripts are plain app files; its calls (rpc, events) still need the token
    if request.method == "OPTIONS" or request.url.path in ("/health", "/mail/oauth/callback") \
            or request.url.path.startswith("/pdfx/app/"):
        return await call_next(request)
    given = request.headers.get("x-vyum-token") or request.query_params.get("token")
    if not given or not secrets.compare_digest(given, TOKEN):
        return JSONResponse({"detail": "invalid token"}, status_code=401)
    return await call_next(request)


# Added after the token check so it wraps it: a 401 still carries CORS headers and reads as a 401 in the UI.
app.add_middleware(CORSMiddleware, allow_origins=["tauri://localhost", "http://tauri.localhost",
                                                  "http://localhost:1420", "http://127.0.0.1:1420"],
                   allow_methods=["*"], allow_headers=["*"], expose_headers=["X-Blocked-Images"])


class Ask(BaseModel):
    question: str
    doc_ids: list[str] | None = None
    mode: str = "accurate"  # accurate | fast
    think: bool = False


class NewProject(BaseModel):
    folder: str
    name: str | None = None


class NewChat(BaseModel):
    project_id: str | None = None


class ChatPatch(BaseModel):
    title: str


class Message(BaseModel):
    text: str
    mode: str = "accurate"      # Documents mode: "accurate" (verified) or "fast"
    think: bool = False
    source: str = "docs"        # "docs": only your files, verified · "chat": general assistant · "compare"
    web: bool = False           # general chat may search the web; nothing else ever does
    run_id: str = ""            # lets the app stop this answer (POST /stop/{run_id})


class SettingsPatch(BaseModel):
    searxng_url: str | None = None


def _public(doc: dict) -> dict:
    return {k: v for k, v in doc.items() if k != "stored_path"}


# jobs -------------------------------------------------------------------------
def _start_job(name: str, work, kind: str = "ingest", **extra) -> dict:
    job_id = uuid.uuid4().hex
    jobs[job_id] = {"id": job_id, "name": name, "kind": kind, "status": "queued", "step": "queued", **extra}

    def run():
        job = jobs[job_id]
        with _ingest_lock:
            job["status"] = "running"
            try:
                job["result"] = work(lambda s: job.update(step=s))
                if kind == "ingest":
                    job["document"] = _public(job["result"])
                job["status"] = "done"
            except Exception as e:
                job.update(status="error", error=f"{type(e).__name__}: {e}")
            finally:
                if not any(j["status"] == "queued" for j in jobs.values()):
                    release_memory()  # last job in the queue: give the memory back to chat

    threading.Thread(target=run, daemon=True).start()
    return jobs[job_id]


async def _upload_job(file: UploadFile, chat_id: str | None = None) -> dict:
    name = Path(file.filename or "document").name
    if Path(name).suffix.lower() not in SUPPORTED:
        raise HTTPException(400, f"{name}: unsupported file type. Supported: {', '.join(sorted(SUPPORTED))}")
    tmp_dir = Path(tempfile.mkdtemp(prefix="vyum-"))
    tmp = tmp_dir / name
    with open(tmp, "wb") as out:  # stream to disk: a multi-gigabyte upload must not sit in memory
        while block := await file.read(1 << 20):
            out.write(block)

    def work(progress):
        try:
            doc = ingest_file(tmp, store, name, progress=progress)
        finally:
            tmp.unlink(missing_ok=True)
            tmp_dir.rmdir()
        if not chat_id:
            store.update(doc["id"], library=True)  # a direct upload belongs to the library, not to a chat or project
            doc = store.document(doc["id"])
        if chat_id:
            chat = ws.chats.get(chat_id)
            if chat and doc["id"] not in chat["doc_ids"]:
                chat["doc_ids"].append(doc["id"])
                ws.chats.put(chat)
        return doc

    job = _start_job(name, work, chat_id=chat_id)
    return job


@app.get("/jobs/{job_id}")
def job(job_id: str):
    if job_id not in jobs:
        raise HTTPException(404, "unknown job")
    return jobs[job_id]


# health and documents ---------------------------------------------------------
@app.get("/health")
def health():
    try:
        have = llm.available_models()
        needed = (config.GEN_MODEL, config.EMBED_MODEL) + ((config.VISION_MODEL,) if config.DESCRIBE_FIGURES else ())
        models = {m: any(h == m or h.startswith(m + ":") for h in have) for m in needed}
        ok = all(models.values())
    except Exception as e:  # model server down
        models, ok = {"error": str(e)}, False
    return {"ok": ok, "models": models, "documents": len(store.documents())}


class SetupStart(BaseModel):
    vision: bool = False


@app.get("/setup")
def setup_status():
    """First launch of an installed Vyum: what still has to be downloaded (always ready in a source checkout)."""
    from . import setup
    return setup.status()


@app.post("/setup")
def setup_start(req: SetupStart):
    from . import setup
    return setup.start(req.vision)


@app.post("/shutdown")
def shutdown():
    """The app is quitting: stop the model server and the PDF editor host before the engine itself is stopped."""
    from . import ollama_host
    ollama_host.stop()
    try:
        from .pdfhost import HOST_PROCESS
        HOST_PROCESS.stop()
    except Exception:
        pass
    return {"ok": True}


class MailDraft(BaseModel):
    kind: str = "new"            # "new", "reply" or "summarize"
    instructions: str = ""
    thread: str = ""
    to: str = ""
    subject: str = ""
    tone: str = "professional"
    length: str = "short"
    sender: str = ""
    doc_ids: list[str] = []      # library documents to draw facts from
    think: bool = False


_STOPS: set[str] = set()
# status lines that mean a file is being written: from here on Stop waits for the write to finish
_WRITING = re.compile(r"^(Saving|Applying|Writing \d|Rebuilding|Building \d|Merging|Exporting|Converting)")


@app.post("/stop/{run_id}")
def stop_run(run_id: str):
    """The Stop button: the running answer ends at its next safe point."""
    _STOPS.add(run_id)
    return {"ok": True}


def stoppable(gen, run_id: str):
    """Yields gen's events until Stop is pressed, then closes it (which also ends the model's generation) and yields
    {"type": "stopped"}. Once a file is being saved ("Saving…") the save always finishes, so a file is never half written."""
    saving = False
    try:
        for ev in gen:
            if ev.get("type") == "status" and _WRITING.search(str(ev.get("text", ""))):
                saving = True
            yield ev
            if run_id and run_id in _STOPS and not saving and ev.get("type") != "result":
                gen.close()
                yield {"type": "stopped"}
                return
    finally:
        _STOPS.discard(run_id)


def _sse(gen):
    def events():
        try:
            for ev in gen:
                yield f"data: {json.dumps(ev)}\n\n"
        except Exception as e:  # our own errors are written for people; anything else shows its type for support
            msg = str(e) if type(e).__name__ in ("OfficeError", "StepError", "MailError") else f"{type(e).__name__}: {e}"
            yield f"data: {json.dumps({'type': 'result', 'result': {'kind': 'error', 'error': msg}})}\n\n"
        yield f"data: {json.dumps({'type': 'done'})}\n\n"
    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/mail/draft")
def mail_draft(req: MailDraft):
    """Writes, answers or summarizes an email. Nothing is sent: the app hands the draft to the user's mail app."""
    if req.kind not in ("new", "reply", "summarize"):
        raise HTTPException(400, "kind must be new, reply or summarize")
    reference = []
    if req.doc_ids and req.kind != "summarize":
        from .retrieval import hybrid_search, rerank
        q = (req.instructions + "\n" + req.thread[:1500]).strip() or req.subject
        rows = hybrid_search(store, [q], req.doc_ids, k=20)
        reference = [r for r in rerank(q, rows, 5) if r["score"] >= 0.05]
    return _sse(mail.draft_events(req.kind, req.instructions, thread=req.thread, to=req.to, subject=req.subject,
                                  tone=req.tone, length=req.length, sender=req.sender, reference=reference,
                                  think=req.think))


def _dir_bytes(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) if path.exists() else 0


@app.get("/overview")
def overview():
    """Everything the Overview page shows, from local state only (no model call, nothing leaves the machine)."""
    docs = store.documents()
    chats = ws.chats.all()
    projects = ws.projects.all()
    by_type: dict[str, int] = {}
    for d in docs:
        by_type[d.get("ext", "?").lstrip(".")] = by_type.get(d.get("ext", "?").lstrip("."), 0) + 1
    day = 86400
    today = int(time.time() // day)
    activity = [0] * 14
    questions = 0
    for c in chats:
        for m in c.get("messages", []):
            if m.get("role") == "user":
                questions += 1
                ago = today - int(m.get("ts", 0) // day)
                if 0 <= ago < 14:
                    activity[13 - ago] += 1
    try:
        searx = httpx.get(web.settings()["searxng_url"] + "/healthz", timeout=0.6).status_code == 200
    except httpx.HTTPError:
        searx = False
    from . import office
    return {
        "documents": len(docs), "indexing": sum(d.get("status") == "indexing" for d in docs),
        "pages": sum(d.get("pages", 0) for d in docs if d.get("unit") != "line"),
        "passages": sum(d.get("chunks", 0) for d in docs), "figures": sum(d.get("figures", 0) for d in docs),
        "by_type": dict(sorted(by_type.items(), key=lambda kv: -kv[1])),
        "projects": len(projects), "chats": len(chats), "questions": questions, "activity_14d": activity,
        "storage_bytes": _dir_bytes(config.DATA_DIR),
        "recent_documents": [{"id": d["id"], "name": d["name"], "ext": d.get("ext"), "added_at": d.get("added_at")}
                             for d in sorted(docs, key=lambda d: d.get("added_at") or 0, reverse=True)[:6]],
        "recent_chats": [_chat_summary(c) for c in sorted(chats, key=lambda c: c.get("updated_at", 0), reverse=True)[:6]],
        "office": office.status(),
        "web": {"searxng_url": web.settings()["searxng_url"], "running": searx},
        "models": {"generator": config.GEN_MODEL, "vision": config.VISION_MODEL, "embeddings": config.EMBED_MODEL,
                   "reranker": config.RERANK_MODEL},
    }


# office -----------------------------------------------------------------------
class OfficeCreate(BaseModel):
    kind: str
    prompt: str
    doc_ids: list[str] = []     # library documents to base the file on
    think: bool = False


class OfficeEdit(BaseModel):
    instruction: str
    think: bool = False
    run_id: str = ""
    with_ids: list[str] = []    # other office files (PDF merge)


class OfficeOpenLocal(BaseModel):
    path: str


def _office_index(fid: str):
    """(Re)indexes an office file into the library so its contents are searchable and askable in chats."""
    from . import office

    def work(progress):
        it = office.get(fid)
        if not it or not it.get("exists"):
            return None
        src = office.text_for_index(it)
        if not src or src[0].suffix.lower() not in SUPPORTED:
            return None
        doc = ingest_file(src[0], store, src[1], progress=progress)
        store.update(doc["id"], library=True, office_id=fid)
        old = it.get("doc_id")
        office._update(fid, doc_id=doc["id"])
        if old and old != doc["id"] and old not in {i.get("doc_id") for i in office.files() if i["id"] != fid}:
            store.delete_document(old)
        return store.document(doc["id"])
    return _start_job(f"index {fid}", work)


def _office_stream(gen):
    """Streams an office action; when it produced or changed a file, indexes that file afterwards."""
    def wrapped():
        for ev in gen:
            if ev["type"] == "result" and ev["result"].get("file"):
                _office_index(ev["result"]["file"]["id"])
            yield ev
    return _sse(wrapped())


def _office_or_404(fid: str):
    from . import office
    it = office.get(fid)
    if not it:
        raise HTTPException(404, "unknown file")
    return office, it


@app.get("/office/files")
def office_files(kind: str | None = None, q: str | None = None):
    from . import office
    items = office.files()
    if kind:
        items = [i for i in items if i["kind"] == kind or (kind == "md" and i["kind"] == "markdown")]
    if q and q.strip():
        low = q.lower().strip()
        by_doc = {i.get("doc_id"): i for i in items if i.get("doc_id")}
        hits = {}
        if by_doc:
            for r in store.keyword_search(q, 30, list(by_doc)):
                hits.setdefault(r["doc_id"], r["text"])
        out = []
        for i in items:
            snippet = hits.get(i.get("doc_id"))
            if low in i["name"].lower() or snippet:
                out.append(i | ({"match": _snippet(snippet, low)} if snippet else {}))
        items = out
    return {"files": items, "status": office.status()}


def _snippet(text: str, low: str, width: int = 160) -> str:
    at = text.lower().find(low.split()[0]) if low.split() else -1
    start = max(0, at - width // 2) if at >= 0 else 0
    return ("…" if start else "") + text[start:start + width].replace("\n", " ") + ("…" if start + width < len(text) else "")


@app.post("/office/create")
def office_create(req: OfficeCreate):
    from . import office
    if req.kind not in office.KINDS:
        raise HTTPException(400, f"kind must be one of {', '.join(office.KINDS)}")
    if not req.prompt.strip():
        raise HTTPException(400, "Describe what to create.")
    reference = []
    if req.doc_ids:
        reference = assistant.file_sources(store, req.prompt, req.doc_ids)
    return _office_stream(office.create_events(req.kind, req.prompt.strip(), reference, req.think))


@app.post("/office/{fid}/edit")
def office_edit(fid: str, req: OfficeEdit):
    office, it = _office_or_404(fid)
    others = [x for x in (office.get(i) for i in req.with_ids) if x and x["kind"] == "pdf"]
    return _office_stream(stoppable(office.edit_events(fid, req.instruction.strip(), req.think, others), req.run_id))


@app.post("/office/{fid}/undo")
def office_undo(fid: str):
    office, it = _office_or_404(fid)
    try:
        office.undo(fid)
    except office.OfficeError as e:
        raise HTTPException(400, str(e))
    _office_index(fid)
    return office.get(fid)


@app.post("/office/{fid}/open")
def office_open(fid: str):
    office, it = _office_or_404(fid)
    office.open_in_genoffice(Path(it["path"]))
    return {"ok": True}


@app.post("/office/{fid}/reveal")
def office_reveal(fid: str):
    office, it = _office_or_404(fid)
    _reveal(Path(it["path"]))
    return {"ok": True}


@app.get("/office/{fid}/preview")
def office_preview(fid: str):
    office, it = _office_or_404(fid)
    try:
        return {"pages": len(office.previews(fid))}
    except office.OfficeError as e:
        raise HTTPException(400, str(e))


@app.get("/office/{fid}/preview/{page}")
def office_preview_page(fid: str, page: int):
    office, it = _office_or_404(fid)
    pages = office.previews(fid)
    if not 1 <= page <= len(pages):
        raise HTTPException(404, "no such page")
    return FileResponse(pages[page - 1], media_type="image/png")


@app.get("/office/{fid}/file")
def office_file(fid: str):
    office, it = _office_or_404(fid)
    return FileResponse(it["path"], filename=it["name"])


class OfficeNew(BaseModel):
    kind: str
    title: str | None = None


class DocSave(BaseModel):
    html: str


class OfficeChat(BaseModel):
    message: str
    history: list[dict] = []
    think: bool = False
    run_id: str = ""
    live: bool = False  # the PDF editor is open beside the chat: PDF edits are made in it, live


@app.post("/office/new")
def office_new(req: OfficeNew):
    from . import office
    try:
        it = office.new_blank(req.kind, req.title)
    except office.OfficeError as e:
        raise HTTPException(400, str(e))
    return office.get(it["id"])


@app.get("/office/{fid}/doc")
def office_doc(fid: str):
    office, it = _office_or_404(fid)
    if it["kind"] != "docx":
        raise HTTPException(400, "not a Word document")
    return office.doc_html(it)


@app.put("/office/{fid}/doc")
def office_doc_save(fid: str, req: DocSave):
    office, it = _office_or_404(fid)
    try:
        r = office.save_doc_html(it, req.html)
    except office.OfficeError as e:
        raise HTTPException(400, str(e))
    if r.get("changed"):
        _office_index(fid)
    return r | {"file": office.get(fid)}


class SheetSave(BaseModel):
    entries: list[dict]   # the grid's edit journal, in order


class ChartSave(BaseModel):
    chart: dict
    delete: bool = False


@app.get("/office/{fid}/sheet")
def office_sheet(fid: str):
    office, it = _office_or_404(fid)
    if it["kind"] != "xlsx":
        raise HTTPException(400, "not a spreadsheet")
    return office.sheet_snapshot(it)


@app.put("/office/{fid}/sheet")
def office_sheet_save(fid: str, req: SheetSave):
    office, it = _office_or_404(fid)
    try:
        r = office.apply_sheet_journal(it, req.entries)
    except office.OfficeError as e:
        raise HTTPException(400, str(e))
    if r.get("changed"):
        _office_index(fid)
    return r | {"file": office.get(fid)}


class PdfOp(BaseModel):
    action: str          # rotate, delete, keep, extract
    pages: list[int]
    degrees: int = 90


@app.post("/office/{fid}/pdf")
def office_pdf_op(fid: str, req: PdfOp):
    office, it = _office_or_404(fid)
    if it["kind"] != "pdf":
        raise HTTPException(400, "not a PDF")
    try:
        out = office.pdf_op(it, req.action, req.pages, req.degrees)
    except office.OfficeError as e:
        raise HTTPException(400, str(e))
    _office_index(out["id"])
    return out


class SlideOp(BaseModel):
    op: str              # text, notes, delete, duplicate, move
    slide: int
    el: str | None = None
    text: str | None = None
    to: int | None = None


@app.get("/office/{fid}/slides")
def office_slides(fid: str):
    office, it = _office_or_404(fid)
    if it["kind"] != "pptx":
        raise HTTPException(400, "not a presentation")
    return office.deck(it)


@app.post("/office/{fid}/slides")
def office_slide_op(fid: str, req: SlideOp):
    office, it = _office_or_404(fid)
    try:
        out = office.slide_op(it, req.op, req.slide, req.el, req.text, req.to)
    except office.OfficeError as e:
        raise HTTPException(400, str(e))
    _office_index(fid)
    return out


@app.post("/office/{fid}/charts")
def office_chart(fid: str, req: ChartSave):
    office, it = _office_or_404(fid)
    return {"charts": office.save_chart(it, req.chart, req.delete)}


@app.post("/office/{fid}/charts/{cid}/embed")
def office_chart_embed(fid: str, cid: str):
    office, it = _office_or_404(fid)
    try:
        return office.embed_chart(it, cid)
    except office.OfficeError as e:
        raise HTTPException(400, str(e))


# PDF editor (GenOffice's PDF module, hosted) ----------------------------------------------------------------------
_pdf_views: dict[int, dict] = {}
_pdf_view_ids = iter(range(1, 1 << 30))


@app.post("/office/{fid}/pdfx/open")
def pdfx_open(fid: str, lang: str = "en", theme: str = "light"):
    """A view of this PDF in the full editor; the app loads /pdfx/app/index.html?view=..&token=.. in a frame."""
    from . import pdfhost
    office, it = _office_or_404(fid)
    if it["kind"] != "pdf":
        raise HTTPException(400, "not a PDF")
    why = pdfhost.HOST_PROCESS.available()
    if why:
        raise HTTPException(503, why)
    view = next(_pdf_view_ids)
    _pdf_views[view] = {"fid": fid, "snapshot": False}
    try:
        pdfhost.HOST_PROCESS.open(view, it["path"], lang, theme)
    except pdfhost.PdfHostError as e:
        raise HTTPException(503, str(e))
    return {"view": view, "path": f"/pdfx/app/index.html?view={view}&fid={fid}"}


@app.post("/pdfx/close")
def pdfx_close(view: int):
    from . import pdfhost
    _pdf_views.pop(view, None)
    pdfhost.HOST_PROCESS.close(view)
    return {"ok": True}


class PdfxCall(BaseModel):
    channel: str
    args: list = []


def _pdf_view(view: int, fid: str | None):
    """The view, re-opened after an engine or editor restart when the page tells us its file (fid)."""
    from . import office, pdfhost
    v = _pdf_views.get(view)
    if v or not fid:
        return v
    it = office.get(fid)
    if not it or it["kind"] != "pdf":
        return None
    _pdf_views[view] = v = {"fid": fid, "snapshot": False}
    pdfhost.HOST_PROCESS.open(view, it["path"])
    return v


@app.post("/pdfx/rpc")
def pdfx_rpc(view: int, req: PdfxCall, fid: str | None = None):
    from . import office, pdfhost
    v = _pdf_view(view, fid)
    if not v:
        return {"ok": False, "error": "This PDF view was closed. Reopen the file."}
    if req.channel in pdfhost.WRITES and not v["snapshot"]:
        it = office.get(v["fid"])
        if it:
            office._snapshot(it, "Edits in the PDF editor")  # one undo point per editing session
        v["snapshot"] = True
    msg = pdfhost.HOST_PROCESS.invoke(view, req.channel, req.args)
    if not msg.get("ok") and msg.get("error") == "unknown PDF view":  # the editor process restarted: re-grant, retry
        it = office.get(v["fid"])
        if it:
            pdfhost.HOST_PROCESS.open(view, it["path"])
            msg = pdfhost.HOST_PROCESS.invoke(view, req.channel, req.args)
    if msg.get("ok") and req.channel in pdfhost.WRITES:
        office._update(v["fid"], updated_at=time.time())
        _office_index(v["fid"])
    return {"ok": bool(msg.get("ok")), "result": msg.get("result"), "error": msg.get("error")}


@app.post("/pdfx/send")
def pdfx_send(view: int, req: PdfxCall, fid: str | None = None):
    from . import pdfhost
    if _pdf_view(view, fid):
        pdfhost.HOST_PROCESS.send(view, req.channel, req.args)
    return {"ok": True}


@app.get("/pdfx/events")
def pdfx_events(view: int, fid: str | None = None):
    from . import pdfhost
    import queue as _q
    _pdf_view(view, fid)
    q = pdfhost.HOST_PROCESS.subscribe(view)

    def stream():
        try:
            while view in _pdf_views:
                try:
                    ev = q.get(timeout=15)
                    yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                except _q.Empty:
                    yield ": keep-alive\n\n"
        finally:
            pdfhost.HOST_PROCESS.unsubscribe(view, q)
    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@app.get("/pdfx/app/{rest:path}")
def pdfx_app(rest: str):
    """GenOffice's PDF screen (built files), with Vyum's bridge script added to its page."""
    from fastapi.responses import HTMLResponse, Response
    from . import pdfhost
    if rest in ("", "index.html"):
        return HTMLResponse(pdfhost.index_html(), headers={"Cache-Control": "no-store"})
    if rest == "vyum-bridge.js":
        return FileResponse(pdfhost.BRIDGE, media_type="text/javascript", headers={"Cache-Control": "no-store"})
    root = pdfhost.RENDERER.resolve()
    target = (root / rest).resolve()
    if root not in target.parents or not target.is_file():
        raise HTTPException(404, "not found")
    media = {".mjs": "text/javascript", ".js": "text/javascript", ".wasm": "application/wasm", ".css": "text/css",
             ".bcmap": "application/octet-stream", ".pfb": "application/octet-stream"}.get(target.suffix)
    return FileResponse(target, media_type=media)


class DashboardRender(BaseModel):
    filters: dict = {}


@app.get("/office/{fid}/dashboards")
def office_dashboards(fid: str):
    office, it = _office_or_404(fid)
    return [{"id": d["id"], "title": d.get("title"), "created_at": d.get("created_at")} for d in it.get("dashboards") or []]


@app.post("/office/{fid}/dashboards/{did}")
def office_dashboard_render(fid: str, did: str, req: DashboardRender):
    """Every KPI, chart and slicer for the current filters, computed from the workbook's rows."""
    from . import dashboard
    office, it = _office_or_404(fid)
    spec = next((d for d in it.get("dashboards") or [] if d.get("id") == did), None)
    if not spec:
        raise HTTPException(404, "unknown dashboard")
    try:
        return dashboard.render(Path(it["path"]), spec, req.filters)
    except dashboard.DashboardError as e:
        raise HTTPException(400, str(e))


class DashboardRename(BaseModel):
    title: str


@app.post("/office/{fid}/dashboards/{did}/rename")
def office_dashboard_rename(fid: str, did: str, req: DashboardRename):
    office, it = _office_or_404(fid)
    title = req.title.strip()[:80] or "Dashboard"
    office._update(fid, dashboards=[dict(d, title=title) if d.get("id") == did else d for d in it.get("dashboards") or []])
    return {"ok": True, "title": title}


class Export(BaseModel):
    filename: str
    data: str          # base64 of the file (a PNG or GIF of a chart, a CSV of a table)


@app.post("/exports")
def save_export(req: Export):
    """Saves an exported chart or table into Documents/Vyum/Exports (never anywhere else) and returns its path."""
    import base64
    name = re.sub(r"[^\w .()\-]+", "", Path(req.filename).name).strip() or "export.png"
    if Path(name).suffix.lower() not in (".png", ".gif", ".csv", ".svg"):
        raise HTTPException(400, "Only PNG, GIF, SVG or CSV exports.")
    folder = config.OFFICE_DIR / "Exports"
    folder.mkdir(parents=True, exist_ok=True)
    dest, k = folder / name, 1
    while dest.exists():
        dest, k = folder / f"{Path(name).stem} ({k}){Path(name).suffix}", k + 1
    dest.write_bytes(base64.b64decode(req.data.split(",", 1)[-1]))
    return {"path": str(dest), "name": dest.name}


class Reveal(BaseModel):
    path: str


@app.post("/exports/reveal")
def reveal_export(req: Reveal):
    p = Path(req.path).resolve()
    if (config.OFFICE_DIR / "Exports").resolve() not in p.parents:
        raise HTTPException(400, "Only exported files.")
    _reveal(p)
    return {"ok": True}


def _reveal(p: Path):
    """Shows the file selected in Finder (macOS) or File Explorer (Windows)."""
    import subprocess
    if config.WINDOWS:
        subprocess.Popen(["explorer", f"/select,{p}"])
    elif config.MAC:
        subprocess.Popen(["open", "-R", str(p)])
    else:
        subprocess.Popen(["xdg-open", str(p.parent)])


@app.delete("/office/{fid}/dashboards/{did}")
def office_dashboard_delete(fid: str, did: str):
    office, it = _office_or_404(fid)
    office._update(fid, dashboards=[d for d in it.get("dashboards") or [] if d.get("id") != did])
    return {"ok": True}


@app.post("/office/{fid}/chat")
def office_chat(fid: str, req: OfficeChat):
    office, it = _office_or_404(fid)
    return _office_stream(stoppable(office.chat_events(fid, req.message.strip(), req.history, req.think, req.live), req.run_id))


@app.post("/office/open-local")
def office_open_local(req: OfficeOpenLocal):
    from . import office
    p = Path(req.path).expanduser()
    if not p.is_file() or p.suffix.lower().lstrip(".") not in office.KINDS:
        raise HTTPException(400, "Choose a .docx, .xlsx, .pptx, .md, .html or .pdf file.")
    it = office.register(p, "opened")
    _office_index(it["id"])
    return office.get(it["id"])


@app.post("/office/upload")
async def office_upload(file: UploadFile = File(...)):
    """For the browser (no file paths): saves a copy into the Vyum documents folder."""
    from . import office
    name = Path(file.filename or "file").name
    ext = Path(name).suffix.lower().lstrip(".")
    if ext not in office.KINDS:
        raise HTTPException(400, "Choose a .docx, .xlsx, .pptx, .md, .html or .pdf file.")
    dest = office.unique_path(Path(name).stem, ext)
    with open(dest, "wb") as out:
        while block := await file.read(1 << 20):
            out.write(block)
    it = office.register(dest, "opened")
    _office_index(it["id"])
    return office.get(it["id"])


@app.delete("/office/{fid}")
def office_delete(fid: str, delete_file: bool = False):
    office, it = _office_or_404(fid)
    office.forget(fid, delete_file)
    if it.get("doc_id"):
        store.delete_document(it["doc_id"])
    return {"deleted": fid}


# mail accounts ----------------------------------------------------------------
class MailAccountIn(BaseModel):
    email: str
    password: str
    provider: str = "gmail"
    name: str = ""
    imap_host: str = ""
    imap_port: int = 993
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_security: str = "ssl"


class MailAct(BaseModel):
    ids: list[int]
    action: str
    target: str | None = None


class MailAssist(BaseModel):
    account: str
    mode: str                     # summarize, reply, write, ask
    thread: str | None = None
    message_id: int | None = None
    question: str = ""
    instructions: str = ""
    tone: str = "friendly"
    think: bool = False
    run_id: str = ""
    ui_lang: str = "en"           # the language the app is shown in: summaries are written in it


class MailDetect(BaseModel):
    email: str


class OAuthStart(BaseModel):
    provider: str
    email: str
    name: str = ""


class OAuthClients(BaseModel):
    google: dict | None = None
    microsoft: dict | None = None


@app.post("/mail/detect")
def mail_detect(req: MailDetect):
    return mailauth.detect(req.email)


@app.get("/mail/oauth/clients")
def mail_oauth_clients():
    return mailauth.public_clients()


@app.put("/mail/oauth/clients")
def mail_oauth_clients_save(req: OAuthClients):
    return mailauth.save_clients(req.model_dump())


_oauth_names: dict[str, str] = {}


@app.post("/mail/oauth/start")
def mail_oauth_start(req: OAuthStart, request: Request):
    if req.provider not in mailauth.OAUTH:
        raise HTTPException(400, "unknown provider")
    port = request.url.port
    host = "localhost" if req.provider == "microsoft" else "127.0.0.1"  # what each provider accepts for desktop apps
    try:
        r = mailauth.start(req.provider, req.email.strip().lower(), f"http://{host}:{port}/mail/oauth/callback")
    except ValueError as e:
        raise HTTPException(400, str(e))
    _oauth_names[r["state"]] = req.name
    return r


@app.get("/mail/oauth/callback")
def mail_oauth_callback(state: str = "", code: str | None = None, error: str | None = None):
    from fastapi.responses import HTMLResponse
    page = ("<!doctype html><meta charset=utf-8><title>Vyum</title><body style='font:16px -apple-system,Arial;display:grid;"
            "place-items:center;height:90vh;color:#202124'><div style='text-align:center;max-width:460px'>{}</div>")
    try:
        got = mailauth.finish(state, code, error)
        mailbox.add_oauth_account(got["email"], got["provider"], _oauth_names.pop(state, ""))
        return HTMLResponse(page.format(f"<h2>You're signed in</h2><p>{got['email']} is now in Vyum. You can close this tab and go back to the app.</p>"))
    except (ValueError, mailbox.MailError) as e:
        p = mailauth._pending.get(state)
        if p:
            p |= {"status": "error", "error": str(e)}
        return HTMLResponse(page.format(f"<h2>Sign-in didn't work</h2><p>{e}</p><p>Go back to Vyum and try again.</p>"), status_code=400)


@app.get("/mail/oauth/poll")
def mail_oauth_poll(state: str):
    return mailauth.poll(state)


@app.get("/mail/providers")
def mail_providers():
    return {k: {"label": v["label"], "help": v["help"], "note": v["note"], "imap": v["imap"], "smtp": v["smtp"]}
            for k, v in mailbox.PROVIDERS.items()}


@app.get("/mail/accounts")
def mail_accounts():
    st = mailbox.status()
    return [a | {"status": st.get(a["id"], {})} for a in mailbox.accounts()]


@app.post("/mail/accounts")
def mail_add_account(req: MailAccountIn):
    try:
        return mailbox.add_account(req.email, req.password, req.provider, req.name, req.imap_host, req.imap_port,
                                   req.smtp_host, req.smtp_port, req.smtp_security)
    except mailbox.MailError as e:
        raise HTTPException(400, str(e))


@app.delete("/mail/accounts/{aid}")
def mail_remove_account(aid: str):
    mailbox.remove_account(aid)
    return {"removed": aid}


@app.post("/mail/sync")
def mail_sync(account: str | None = None):
    mailbox.start_sync(account)
    return mailbox.status()


@app.get("/mail/{aid}/folders")
def mail_folders(aid: str):
    return mailbox.folders(aid)


@app.get("/mail/{aid}/messages")
def mail_messages(aid: str, folder: str | None = None, q: str = "", page: int = 0, starred: bool = False):
    return mailbox.search_messages(aid, folder, q, page, 50, starred) | {"status": mailbox.status().get(aid, {})}


@app.get("/mail/{aid}/thread")
def mail_thread(aid: str, key: str):
    return mailbox.thread(aid, key)


@app.get("/mail/message/{mid}/html")
def mail_html(mid: int, request: Request, images: bool = False):
    from fastapi.responses import HTMLResponse
    tok = request.query_params.get("token", "")
    html, blocked = mailbox.safe_html(mid, images, cid_url=f"/mail/message/{mid}/cid/{{cid}}?token={tok}")
    return HTMLResponse(html, headers={"X-Blocked-Images": str(blocked),
                                       "Content-Security-Policy": "script-src 'none'; object-src 'none'; frame-src 'none'"})


@app.get("/mail/message/{mid}/blocked")
def mail_blocked(mid: int):
    return {"blocked": mailbox.safe_html(mid, False)[1]}


@app.get("/mail/message/{mid}/cid/{cid}")
def mail_cid(mid: int, cid: str):
    from fastapi.responses import Response
    data, ctype = mailbox.cid(mid, cid)
    return Response(data, media_type=ctype)


@app.get("/mail/message/{mid}/attachment/{index}")
def mail_attachment(mid: int, index: int):
    from fastapi.responses import Response
    from urllib.parse import quote
    data, ctype, name = mailbox.attachment(mid, index)
    return Response(data, media_type=ctype, headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})


@app.post("/mail/{aid}/act")
def mail_act(aid: str, req: MailAct):
    try:
        return mailbox.act(aid, req.ids, req.action, req.target)
    except mailbox.MailError as e:
        raise HTTPException(400, str(e))


@app.post("/mail/{aid}/send")
async def mail_send(aid: str, request: Request):
    form = await request.form()
    files = []
    for f in form.getlist("files"):
        files.append((f.filename, await f.read(), f.content_type or "application/octet-stream"))
    try:
        return mailbox.send(aid, str(form.get("to", "")), str(form.get("subject", "")), str(form.get("text", "")),
                            str(form.get("html") or "") or None, str(form.get("cc", "")), str(form.get("bcc", "")),
                            int(form["reply_to_id"]) if form.get("reply_to_id") else None, files)
    except mailbox.MailError as e:
        raise HTTPException(400, str(e))


@app.post("/mail/assist")
def mail_assist(req: MailAssist):
    """Summarize a conversation, draft a reply, write an email, or answer a question across the mailbox."""
    from .harness import _generate, _status

    def gen():
        t0, timings = time.time(), {}
        if req.mode == "ask":
            yield _status("Searching your mail")
            ctx = mailbox.ask_context(req.account, req.question)
            block = "\n\n".join(f"[{i}] {mailbox.describe(r)}\n{(r['text'] or '')[:1500]}" for i, r in enumerate(ctx, 1))
            msgs = [{"role": "system", "content": mailbox.ASK_RULES},
                    {"role": "user", "content": f"Messages:\n\n{block}\n\nQuestion: {req.question}"}]
            refs = [{"n": i, "id": r["id"], "thread": r["thread"], "subject": r["subject"], "from": r["from_name"] or r["from_addr"],
                     "date_ts": r["date_ts"]} for i, r in enumerate(ctx, 1)]
        elif req.mode in ("summarize", "reply"):
            th = mailbox.thread(req.account, req.thread or "")
            convo = "\n\n---\n\n".join(f"{mailbox.describe(m)}\n{(m['text'] or '')[:4000]}" for m in th)[-20000:]
            if req.mode == "summarize":
                from . import lang as L
                msgs = [{"role": "system", "content": mailbox.THREAD_RULES.format(lang=L.NAMES.get(req.ui_lang, "English"))},
                        {"role": "user", "content": convo}]
            else:
                me = mailbox.account(req.account)
                rules = mail.WRITE_RULES.format(today=time.strftime("%A, %d %B %Y"), tone=req.tone, length=mail.LENGTHS["short"],
                                                sender=me.get("name") or "")
                msgs = [{"role": "system", "content": rules + "\nWrite only the body of the reply (no Subject line)."},
                        {"role": "user", "content": f"Conversation:\n\n{convo}\n\nWhat the reply should say: {req.instructions or 'a helpful reply'}"}]
            refs = []
        else:  # write a new email
            me = mailbox.account(req.account)
            rules = mail.WRITE_RULES.format(today=time.strftime("%A, %d %B %Y"), tone=req.tone, length=mail.LENGTHS["short"],
                                            sender=me.get("name") or "")
            msgs = [{"role": "system", "content": rules}, {"role": "user", "content": req.instructions}]
            refs = []
        yield _status("Writing" if req.mode in ("reply", "write") else "Reading")
        raw, thinking = "", ""
        for ev in _generate(msgs, req.think, timings, max_tokens=1500):
            if ev["type"] == "generated":
                raw, thinking = ev["raw"], ev["thinking"]
            else:
                yield ev
        timings["total_s"] = round(time.time() - t0, 2)
        out = {"kind": "mail_ai", "mode": req.mode, "text": raw, "refs": refs, "timings": timings, "thinking": thinking or None}
        if req.mode == "write":
            m = re.match(r"\s*\**subject\**\s*:\s*(.+?)\s*\n(.*)", raw, re.I | re.S)
            out |= {"subject": m.group(1).strip() if m else "", "text": (m.group(2) if m else raw).strip()}
        yield {"type": "result", "result": out}
    return _sse(stoppable(gen(), req.run_id))


@app.get("/commands")
def list_commands():
    return commands.COMMANDS


@app.get("/documents")
def documents():
    return [_public(d) for d in store.documents()]


@app.delete("/documents/{doc_id}")
def delete_document(doc_id: str):
    if not store.document(doc_id):
        raise HTTPException(404, "unknown document")
    store.delete_document(doc_id)
    return {"deleted": doc_id}


@app.get("/documents/{doc_id}/file")
def document_file(doc_id: str):
    doc = store.document(doc_id)
    if not doc:
        raise HTTPException(404, "unknown document")
    return FileResponse(doc["stored_path"], filename=Path(doc["name"]).name, content_disposition_type="inline")


@app.get("/documents/{doc_id}/page/{page}")
def document_page(doc_id: str, page: int):
    """One page as its own small PDF: the viewer never loads an 11,000-page file to show a citation."""
    import io
    import pypdfium2 as pdfium
    from fastapi.responses import Response
    doc = store.document(doc_id)
    if not doc or doc.get("ext") != ".pdf":
        raise HTTPException(404, "unknown PDF")
    from .pdflock import PDFIUM
    buf = io.BytesIO()
    with PDFIUM:
        src = pdfium.PdfDocument(doc["stored_path"])
        if not 1 <= page <= len(src):
            raise HTTPException(404, f"page {page} is outside 1–{len(src)}")
        out = pdfium.PdfDocument.new()
        out.import_pages(src, [page - 1])
        out.save(buf)
    return Response(buf.getvalue(), media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="page-{page}.pdf"'})


def resume_interrupted():
    """At startup, finish any ingest that stopped part-way (crash, quit, power loss) from its last checkpoint."""
    for doc in store.documents():
        if doc.get("status") == "indexing" and Path(doc.get("stored_path", "")).exists():
            _start_job(f"Resume {doc['name']}", lambda progress, d=doc: ingest_file(
                d["stored_path"], store, d["name"], progress=progress), resumed=True)


@app.post("/ingest")
async def ingest(file: UploadFile = File(...)):
    job = await _upload_job(file)
    return {**job, "document": None}


resume_interrupted()


# one-shot endpoints used by the eval ---------------------------------------------
@app.post("/chat")
def chat_once(req: Ask):
    if not req.question.strip():
        raise HTTPException(400, "empty question")
    return harness.answer(store, req.question.strip(), req.doc_ids or None, req.mode, req.think)


@app.post("/compare")
def compare_once(req: Ask):
    try:
        return harness.compare(store, req.question.strip(), req.doc_ids or [], req.mode, req.think)
    except ValueError as e:
        raise HTTPException(400, str(e))


# projects ---------------------------------------------------------------------
def _project_view(p: dict) -> dict:
    files = []
    for rel, f in sorted(p.get("files", {}).items()):
        doc = store.document(f["doc_id"]) if f.get("doc_id") else None
        files.append({"path": rel, "error": f.get("error"), **({"doc": _public(doc)} if doc else {})})
    running = next((j for j in jobs.values() if j.get("project_id") == p["id"]
                    and j["status"] in ("queued", "running")), None)
    return {k: v for k, v in p.items() if k != "files"} | {"files": files, "scan": running}


def _scan_job(pid: str) -> dict:
    p = ws.projects.get(pid)
    return _start_job(f"Scan {p['name']}", lambda progress: ws.scan_project(pid, progress), kind="scan",
                      project_id=pid)


@app.get("/projects")
def list_projects():
    return sorted((_project_view(p) for p in ws.projects.all()), key=lambda p: -p["created_at"])


@app.post("/projects")
def create_project(req: NewProject):
    try:
        p = ws.create_project(req.folder, req.name)
    except ValueError as e:
        raise HTTPException(400, str(e))
    _scan_job(p["id"])
    return _project_view(ws.projects.get(p["id"]))


@app.get("/projects/{pid}")
def get_project(pid: str):
    p = ws.projects.get(pid)
    if not p:
        raise HTTPException(404, "unknown project")
    return _project_view(p)


@app.post("/projects/{pid}/scan")
def scan_project(pid: str):
    if not ws.projects.get(pid):
        raise HTTPException(404, "unknown project")
    return _scan_job(pid)


@app.delete("/projects/{pid}")
def delete_project(pid: str):
    ws.delete_project(pid)
    return {"deleted": pid}


# chats ------------------------------------------------------------------------
def _chat_summary(c: dict) -> dict:
    return {k: c[k] for k in ("id", "title", "project_id", "created_at", "updated_at")} | {
        "files": len(c.get("doc_ids", [])), "messages": len(c.get("messages", []))}


@app.get("/chats")
def list_chats(project_id: str | None = None):
    chats = [c for c in ws.chats.all() if c.get("project_id") == project_id]
    return sorted((_chat_summary(c) for c in chats), key=lambda c: -c["updated_at"])


@app.get("/settings")
def get_settings():
    return web.public_settings()


@app.put("/settings")
def put_settings(req: SettingsPatch):
    try:
        web.save_settings(req.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e))
    return web.public_settings()


@app.post("/settings/check")
def check_settings(req: SettingsPatch):
    """Tries a search against the given SearXNG address (the saved one if none is given)."""
    return web.check(req.searxng_url)


@app.post("/chats")
def create_chat(req: NewChat):
    if req.project_id and not ws.projects.get(req.project_id):
        raise HTTPException(404, "unknown project")
    return _chat_view(ws.create_chat(req.project_id))


def _chat_view(c: dict) -> dict:
    """A chat as the app reads it: the stored record plus its attached files (always a list)."""
    return c | {"files": [_public(d) for d in (store.document(i) for i in c.get("doc_ids", [])) if d]}


@app.get("/chats/{cid}")
def get_chat(cid: str):
    c = ws.chats.get(cid)
    if not c:
        raise HTTPException(404, "unknown chat")
    return _chat_view(c)


@app.patch("/chats/{cid}")
def rename_chat(cid: str, req: ChatPatch):
    c = ws.chats.get(cid)
    if not c:
        raise HTTPException(404, "unknown chat")
    c["title"] = req.title.strip()[:120] or c["title"]
    return ws.chats.put(c)


@app.delete("/chats/{cid}")
def delete_chat(cid: str):
    ws.delete_chat(cid)
    return {"deleted": cid}


@app.post("/chats/{cid}/files")
async def attach_files(cid: str, files: list[UploadFile] = File(...)):
    if not ws.chats.get(cid):
        raise HTTPException(404, "unknown chat")
    return [await _upload_job(f, chat_id=cid) for f in files]


@app.delete("/chats/{cid}/files/{doc_id}")
def detach_file(cid: str, doc_id: str):
    c = ws.chats.get(cid)
    if not c:
        raise HTTPException(404, "unknown chat")
    c["doc_ids"] = [d for d in c["doc_ids"] if d != doc_id]
    ws.chats.put(c)
    ws._drop_if_unused(doc_id)
    return {"detached": doc_id}


@app.post("/chats/{cid}/messages")
def send_message(cid: str, req: Message):
    """Streams server-sent events: status, sources, thinking, thinking_done, draft, then result and done."""
    chat = ws.chats.get(cid)
    if not chat:
        raise HTTPException(404, "unknown chat")
    text = req.text.strip()
    if not text:
        raise HTTPException(400, "empty message")

    doc_ids = ws.chat_scope(chat)
    docs = [d for d in (store.documents() if doc_ids is None else (store.document(i) for i in doc_ids)) if d]
    project = ws.projects.get(chat["project_id"]) if chat.get("project_id") else None
    pairs = [(m["text"], reply.get("result") or {}) for m, reply in zip(chat["messages"], chat["messages"][1:])
             if m["role"] == "user" and reply["role"] == "assistant" and not commands.is_command(m["text"])]
    history = [(q, r.get("answer", "")) for q, r in pairs if r.get("kind") == "answer" and r.get("status") == "answered"]
    # general chat also remembers its own replies and what Documents mode could not find
    chat_history = [(q, r.get("answer", "")) for q, r in pairs if r.get("kind") in ("answer", "chat") and r.get("answer")]
    source = req.source if req.source in ("docs", "chat", "compare") else "docs"
    chat["messages"].append({"role": "user", "text": text, "ts": time.time()})
    if chat["title"] == "New chat":
        chat["title"] = text[:60] + ("…" if len(text) > 60 else "")
    ws.chats.put(chat)

    def events():
        result, partial, cites, calcs = None, [], [], []
        try:
            is_cmd = commands.is_command(text)
            quick = None if is_cmd or source != "docs" else commands.quick_reply(text, docs, in_project=project is not None)
            if quick:
                gen = iter([{"type": "result", "result": quick}])
            elif is_cmd or source == "compare":
                gen = commands.run(text if is_cmd else f"/compare {text}", store=store, docs=docs, doc_ids=doc_ids,
                                   mode=req.mode, think=req.think, project=project, compare_all=not is_cmd,
                                   start_scan=(lambda: _scan_job(project["id"])) if project else None)
            elif source == "chat":
                ready = [d for d in docs if d.get("status", "ready") == "ready" or d.get("chunks")]
                in_scope = doc_ids is not None or project is not None  # files attached here, not the whole library
                gen = assistant.chat_events(store, text, doc_ids, use_docs=bool(ready) and in_scope, use_web=req.web,
                                            think=req.think, history=chat_history,
                                            use_library=bool(ready) and not in_scope)
            else:
                gen = (ev if ev["type"] != "result" else {"type": "result", "result": {"kind": "answer", **ev["result"]}}
                       for ev in harness.answer_events(store, text, doc_ids, req.mode, req.think, history))
            for ev in stoppable(gen, req.run_id):
                if ev["type"] == "result":
                    result = ev["result"]
                elif ev["type"] == "draft":
                    partial.append(ev["text"])
                elif ev["type"] == "sources":
                    cites = ev.get("citations") or []
                elif ev["type"] == "calc":
                    calcs = ev.get("steps") or []
                elif ev["type"] == "stopped":
                    # what was written so far stays, marked as stopped (and not verified in Documents mode)
                    result = {"kind": "chat", "answer": "".join(partial).strip(), "citations": cites, "stopped": True,
                              "unverified": source != "chat", "timings": {}, "calculations": calcs}
                    ev = {"type": "result", "result": result}
                yield f"data: {json.dumps(ev)}\n\n"
        except Exception as e:
            result = {"kind": "error", "error": f"{type(e).__name__}: {e}"}
            yield f"data: {json.dumps({'type': 'result', 'result': result})}\n\n"
        finally:
            fresh = ws.chats.get(cid)
            if fresh is not None:
                fresh["messages"].append({"role": "assistant", "result": result or {"kind": "error",
                                          "error": "stopped"}, "ts": time.time()})
                fresh["updated_at"] = time.time()
                ws.chats.put(fresh)
        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
