"""Projects (a folder kept in sync with the index) and chats (conversations with attached files)."""
import json
import os
import threading
import time
import uuid
from pathlib import Path

from . import config
from .ingest import SUPPORTED, ingest_file
from .store import Store

_lock = threading.RLock()
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".idea", ".vscode", "dist", "build"}


class _JsonDir:
    """One JSON file per record, written atomically."""

    def __init__(self, path: Path):
        self.path = path
        self.path.mkdir(parents=True, exist_ok=True)

    def all(self) -> list[dict]:
        with _lock:
            out = []
            for f in self.path.glob("*.json"):
                try:
                    out.append(json.loads(f.read_text()))
                except (OSError, json.JSONDecodeError):
                    continue
            return out

    def get(self, rid: str) -> dict | None:
        f = self.path / f"{Path(rid).name}.json"
        with _lock:
            return json.loads(f.read_text()) if f.exists() else None

    def put(self, rec: dict) -> dict:
        with _lock:
            tmp = self.path / f"{rec['id']}.tmp"
            tmp.write_text(json.dumps(rec, indent=1))
            tmp.replace(self.path / f"{rec['id']}.json")
        return rec

    def delete(self, rid: str):
        with _lock:
            (self.path / f"{Path(rid).name}.json").unlink(missing_ok=True)


class Workspace:
    def __init__(self, store: Store):
        self.store = store
        self.projects = _JsonDir(store.dir / "projects")
        self.chats = _JsonDir(store.dir / "chats")

    # references ----------------------------------------------------------------
    def referenced_doc_ids(self) -> set[str]:
        ids = {d["id"] for d in self.store.documents() if d.get("library")}  # uploaded on their own: kept
        for p in self.projects.all():
            ids |= {f["doc_id"] for f in p.get("files", {}).values() if f.get("doc_id")}
        for c in self.chats.all():
            ids |= set(c.get("doc_ids", []))
        return ids

    def _drop_if_unused(self, doc_id: str):
        if doc_id not in self.referenced_doc_ids():
            self.store.delete_document(doc_id)

    # projects ------------------------------------------------------------------
    def create_project(self, folder: str, name: str | None = None) -> dict:
        path = Path(folder).expanduser().resolve()
        if not path.is_dir():
            raise ValueError(f"Not a folder: {folder}")
        rec = {"id": uuid.uuid4().hex[:12], "name": name or path.name, "folder": str(path),
               "files": {}, "created_at": time.time(), "last_scan": None}
        return self.projects.put(rec)

    def project_doc_ids(self, project: dict) -> list[str]:
        return [f["doc_id"] for f in project.get("files", {}).values() if f.get("doc_id")]

    def delete_project(self, pid: str):
        project = self.projects.get(pid)
        if not project:
            return
        for chat in self.chats.all():
            if chat.get("project_id") == pid:
                self.chats.delete(chat["id"])
        self.projects.delete(pid)
        for doc_id in self.project_doc_ids(project):
            self._drop_if_unused(doc_id)

    def scan_project(self, pid: str, progress=lambda msg: None) -> dict:
        """Bring the index in line with the folder: ingest new and changed files, drop deleted ones."""
        project = self.projects.get(pid)
        root = Path(project["folder"])
        if not root.is_dir():
            raise ValueError(f"Folder no longer exists: {root}")
        found = {}
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
            for fn in filenames:
                p = Path(dirpath) / fn
                if fn.startswith(".") or p.suffix.lower() not in SUPPORTED:
                    continue
                st = p.stat()
                if st.st_size > config.MAX_FILE_MB * 1024 * 1024:
                    continue
                found[str(p.relative_to(root))] = (p, f"{st.st_size}:{int(st.st_mtime)}")

        files = project.get("files", {})
        added = changed = removed = failed = 0
        todo = [(rel, p, sig) for rel, (p, sig) in sorted(found.items())
                if rel not in files or files[rel].get("sig") != sig or files[rel].get("error")]
        for i, (rel, p, sig) in enumerate(todo, 1):
            progress(f"reading {rel} ({i}/{len(todo)})")
            old = files.get(rel, {}).get("doc_id")
            try:
                doc = ingest_file(p, self.store, display_name=rel,
                                  progress=lambda s, rel=rel, i=i: progress(f"{rel} ({i}/{len(todo)}): {s}"))
                files[rel] = {"doc_id": doc["id"], "sig": sig, "error": None}
                if old is None:
                    added += 1
                else:
                    changed += 1
            except Exception as e:  # one broken file must not stop the scan
                files[rel] = {"doc_id": None, "sig": sig, "error": f"{type(e).__name__}: {e}"}
                failed += 1
            project["files"] = files
            self.projects.put(project)
            if old and old != files[rel]["doc_id"]:
                self._drop_if_unused(old)
        for rel in [r for r in files if r not in found]:
            doc_id = files.pop(rel).get("doc_id")
            project["files"] = files
            self.projects.put(project)
            if doc_id:
                self._drop_if_unused(doc_id)
            removed += 1
        project["last_scan"] = time.time()
        self.projects.put(project)
        return {"added": added, "changed": changed, "removed": removed, "failed": failed,
                "unchanged": len(found) - len(todo), "total": len(found)}

    # chats ---------------------------------------------------------------------
    def create_chat(self, project_id: str | None = None) -> dict:
        now = time.time()
        return self.chats.put({"id": uuid.uuid4().hex[:12], "title": "New chat", "project_id": project_id,
                               "doc_ids": [], "messages": [], "created_at": now, "updated_at": now})

    def chat_scope(self, chat: dict) -> list[str] | None:
        """Doc ids a chat searches: project files + attachments; None means every document."""
        ids = list(chat.get("doc_ids", []))
        if chat.get("project_id"):
            project = self.projects.get(chat["project_id"])
            ids += self.project_doc_ids(project) if project else []
        return sorted(set(ids)) or (None if not chat.get("project_id") else [])

    def delete_chat(self, cid: str):
        chat = self.chats.get(cid)
        self.chats.delete(cid)
        for doc_id in (chat or {}).get("doc_ids", []):
            self._drop_if_unused(doc_id)
