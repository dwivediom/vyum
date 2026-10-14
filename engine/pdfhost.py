"""The PDF editor: GenOffice's PDF module (Apache-2.0) hosted in Vyum.

GenOffice's PDF back end runs unchanged in a windowless Electron process (pdfhost/host.ts, built by
pdfhost/build.mjs); its own PDF screen is served by this engine with a bridge (pdfhost/bridge.ts) that turns the
screen's Electron calls into HTTP calls here. This module starts that process and passes messages both ways.
Nothing in it talks to the network: the host's cloud AI and image channels are switched off, and the screen's own
content security policy only lets it reach this engine.
"""
import itertools
import json
import os
import queue
import subprocess
import threading
from pathlib import Path

from . import config

ROOT = Path(__file__).resolve().parents[1]
HOST = ROOT / "pdfhost" / "dist" / "host.cjs"
BRIDGE = ROOT / "pdfhost" / "dist" / "vyum-bridge.js"
RENDERER = config.GENOFFICE_SRC / "apps" / "pdf" / "out" / "renderer"
ELECTRON = config.GENOFFICE_SRC / "node_modules" / "electron" / "dist" / "Electron.app" / "Contents" / "MacOS" / "Electron"
# channels that write the PDF: Vyum keeps an undo version before the first one in each editing session
WRITES = {"pdf:save", "pdf:insert-pdf", "pdf:insert-blank-page", "pdf:merge-pages", "pdf:replace-pages",
          "pdf:set-page-size", "pdf:split-pages", "pdf:crop-pages", "pdf:request-redaction-copy"}


class PdfHostError(RuntimeError):
    pass


class PdfHost:
    def __init__(self):
        self.proc: subprocess.Popen | None = None
        self.lock = threading.Lock()
        self.ids = itertools.count(1)
        self.waiting: dict[int, dict] = {}
        self.events: dict[int, list[queue.Queue]] = {}
        self.ready = threading.Event()

    def available(self) -> str | None:
        """Why the editor cannot start, or None."""
        for p in (ELECTRON, HOST, RENDERER / "index.html"):
            if not p.exists():
                return "The PDF editor is not installed yet. Run scripts/build_genoffice.sh and node pdfhost/build.mjs once."
        return None

    def _start(self):
        if self.proc and self.proc.poll() is None:
            return
        why = self.available()
        if why:
            raise PdfHostError(why)
        self.ready.clear()
        env = {**os.environ, "NODE_PATH": str(config.GENOFFICE_SRC / "node_modules"), "ELECTRON_NO_ATTACH_CONSOLE": "1",
               "VYUM_GENOFFICE_SRC": str(config.GENOFFICE_SRC),  # where GenOffice's wasm files are found
               # GenOffice's AI features run on Vyum's on-device model
               "VYUM_AI_URL": config.LLM_BASE_URL, "VYUM_AI_MODEL": config.GEN_MODEL}
        self.proc = subprocess.Popen([str(ELECTRON), str(HOST)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, env=env, text=True, bufsize=1)
        threading.Thread(target=self._read, args=(self.proc,), daemon=True).start()
        if not self.ready.wait(30):
            raise PdfHostError("The PDF editor did not start.")

    def _read(self, proc):
        for line in proc.stdout:
            if not line.startswith("\x01"):
                continue  # library logging, not protocol
            try:
                m = json.loads(line[1:])
            except json.JSONDecodeError:
                continue
            op = m.get("op")
            if op == "ready":
                self.ready.set()
            elif op == "reply":
                w = self.waiting.get(m.get("id"))
                if w:
                    w["msg"] = m
                    w["done"].set()
            elif op == "event":
                for q in list(self.events.get(m.get("view"), [])):
                    q.put({"channel": m["channel"], "args": m.get("args", [])})
        for w in list(self.waiting.values()):  # the process ended: nobody waits forever
            w["msg"] = {"ok": False, "error": "The PDF editor stopped."}
            w["done"].set()

    def _write(self, msg: dict):
        with self.lock:
            self._start()
            self.proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
            self.proc.stdin.flush()

    def open(self, view: int, path: str, lang: str = "en", theme: str = "light"):
        self._write({"op": "open", "view": view, "path": path, "lang": lang, "theme": theme})

    def close(self, view: int):
        if self.proc and self.proc.poll() is None:
            self._write({"op": "close", "view": view})

    def invoke(self, view: int, channel: str, args: list, timeout: float = 600) -> dict:
        i = next(self.ids)
        w = {"done": threading.Event(), "msg": None}
        self.waiting[i] = w
        try:
            self._write({"op": "invoke", "id": i, "view": view, "channel": channel, "args": args})
            if not w["done"].wait(timeout):
                return {"ok": False, "error": f"{channel} took too long"}
            return w["msg"]
        finally:
            self.waiting.pop(i, None)

    def send(self, view: int, channel: str, args: list):
        self._write({"op": "send", "view": view, "channel": channel, "args": args})

    def subscribe(self, view: int) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        self.events.setdefault(view, []).append(q)
        return q

    def unsubscribe(self, view: int, q: queue.Queue):
        if q in self.events.get(view, []):
            self.events[view].remove(q)


HOST_PROCESS = PdfHost()


def index_html() -> str:
    """GenOffice's PDF page with Vyum's bridge loaded first (a classic script runs before the app's module)."""
    html = (RENDERER / "index.html").read_text(encoding="utf-8")
    return html.replace('<script type="module"', '<script src="./vyum-bridge.js"></script>\n    <script type="module"', 1) \
               .replace("<title>GenOffice PDF</title>", "<title>Vyum PDF</title>")
