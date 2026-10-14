"""First launch of an installed Vyum: downloads the model server (Ollama) and the models once; afterwards Vyum works
fully offline. The installer stays small (about 1 GB) and the person sees what is downloaded and how big it is.

The downloads run in their own process (`python -m engine.setup run`), so the engine itself never goes online:
it starts that process and reads its progress (one JSON object per line).

Model choice by memory: 16 GB or more -> Qwen3-8B; less -> Qwen3-4B (both Apache-2.0). Reading pictures (Qwen2.5-VL,
6 GB) is optional. Every file comes from its publisher: Ollama's GitHub release (checksum pinned here), the Ollama
model registry (verified by Ollama), and Hugging Face (reranker, Docling and OCR models).
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import threading
import time
import zipfile
from pathlib import Path

from . import config, ollama_host

OLLAMA_VERSION = "v0.40.4"
OLLAMA_FILES = {  # platform -> (release file, sha256 from the release's sha256sum.txt, bytes)
    "darwin": ("ollama-darwin.tgz", "a4ceae4b2aaf18df37ebcbfb56ba3a37a87838631112d66c2be07268693c8d58", 168_000_000),
    "win32": ("ollama-windows-amd64.zip", "7485a1172f8527db4342a63521b9342fc466185ff0855da378cc864ecf6354cf", 1_470_000_000),
}
TIERS = {  # name -> (Ollama model, Vyum alias with an 8K context, download bytes)
    "8b": ("qwen3:8b", "vyum-qwen3-8b", 5_200_000_000),
    # the instruct edition: plain "qwen3:4b" is now the thinking-only 2507 model, which always reasons first and
    # loops in JSON mode
    "4b": ("qwen3:4b-instruct-2507-q4_K_M", "vyum-qwen3-4b", 2_500_000_000),
}
EMBED = ("bge-m3", 1_200_000_000)
VISION = ("qwen2.5vl:7b", "vyum-qwen25vl-7b", 6_000_000_000)
RERANKER_BYTES = 2_270_000_000
DOCLING_BYTES = 520_000_000


# what this computer gets ---------------------------------------------------------------------------------------------
def ram_gb() -> float:
    try:
        if config.MAC:
            out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True).stdout
            return int(out) / 2**30
        if config.WINDOWS:
            import ctypes

            class Mem(ctypes.Structure):
                _fields_ = [("len", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
                            ("avail", ctypes.c_ulonglong), ("tpf", ctypes.c_ulonglong), ("apf", ctypes.c_ulonglong),
                            ("tv", ctypes.c_ulonglong), ("av", ctypes.c_ulonglong), ("ave", ctypes.c_ulonglong)]
            m = Mem()
            m.len = ctypes.sizeof(Mem)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return m.total / 2**30
        with open("/proc/meminfo") as f:
            return int(f.readline().split()[1]) / 2**20
    except Exception:
        return 16.0


def tier() -> str:
    forced = os.environ.get("VYUM_TIER")
    if forced in TIERS:
        return forced
    return "8b" if ram_gb() >= 15 else "4b"  # "16 GB" machines report a little under 16


def gen_alias() -> str:
    return TIERS[tier()][1]


def _prefs_path() -> Path:
    return config.DATA_DIR / "setup.json"


def prefs() -> dict:
    try:
        return json.loads(_prefs_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_prefs(**kw):
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    _prefs_path().write_text(json.dumps({**prefs(), **kw}), encoding="utf-8")


# what is already here ---------------------------------------------------------------------------------------------
def _ollama_has(model: str) -> bool:
    import httpx
    try:
        r = httpx.get(f"http://127.0.0.1:{ollama_host.port()}/api/tags", timeout=3)
        names = {m["name"].removesuffix(":latest") for m in r.json().get("models", [])}
        return model.removesuffix(":latest") in names
    except Exception:
        return False


def _reranker_ready() -> bool:
    try:
        from huggingface_hub import try_to_load_from_cache
        return isinstance(try_to_load_from_cache(config.RERANK_MODEL, "model.safetensors"), str)
    except Exception:
        return False


def _docling_ready() -> bool:
    d = config.DOCLING_MODELS
    return (d / "docling-project--docling-layout-heron").is_dir() and (d / "docling-project--docling-models").is_dir()


def items(with_vision: bool | None = None) -> list[dict]:
    """Everything setup installs, in order, with whether it is already here."""
    if with_vision is None:
        with_vision = bool(prefs().get("vision"))
    t = tier()
    model, alias, gen_bytes = TIERS[t]
    plat = OLLAMA_FILES.get(sys.platform)
    out = [
        {"id": "ollama", "label": "Model server (Ollama)", "bytes": plat[2] if plat else 0,
         "done": ollama_host.binary().exists()},
        {"id": "gen", "label": f"Language model ({'Qwen3-8B' if t == '8b' else 'Qwen3-4B'})", "bytes": gen_bytes,
         "done": _ollama_has(alias)},
        {"id": "embed", "label": "Search model (BGE-M3)", "bytes": EMBED[1], "done": _ollama_has(EMBED[0])},
        {"id": "reranker", "label": "Answer checker (BGE reranker)", "bytes": RERANKER_BYTES, "done": _reranker_ready()},
        {"id": "docling", "label": "Document reader (Docling)", "bytes": DOCLING_BYTES, "done": _docling_ready()},
    ]
    if with_vision:
        out.append({"id": "vision", "label": "Picture reader (Qwen2.5-VL, optional)", "bytes": VISION[2],
                    "done": _ollama_has(VISION[1])})
    return out


# the engine's view: status and the background download process -------------------------------------------------------
_state = {"proc": None, "progress": {}, "error": None, "log": []}
_state_lock = threading.Lock()


def status() -> dict:
    if not config.PACKAGED:
        return {"packaged": False, "ready": True, "items": []}
    ollama_host.start()
    its = items()
    with _state_lock:
        proc = _state["proc"]
        running = proc is not None and proc.poll() is None
        progress = dict(_state["progress"])
        error = _state["error"]
    for it in its:
        p = progress.get(it["id"])
        if p and not it["done"]:
            it["got"] = p.get("got", 0)
            it["state"] = p.get("state", "waiting")
    return {"packaged": True, "ready": all(i["done"] for i in its) and not running, "running": running,
            "error": error, "tier": tier(), "ram_gb": round(ram_gb(), 1), "vision": bool(prefs().get("vision")),
            "items": its, "total_bytes": sum(i["bytes"] for i in its if not i["done"])}


def start(with_vision: bool = False) -> dict:
    """Starts the download process (if it is not already running)."""
    _save_prefs(vision=bool(with_vision))
    with _state_lock:
        proc = _state["proc"]
        if proc is not None and proc.poll() is None:
            return status()
        _state.update(progress={}, error=None, log=[])
        env = {**os.environ, "HF_HUB_OFFLINE": "0", "TRANSFORMERS_OFFLINE": "0"}
        proc = subprocess.Popen([sys.executable, "-m", "engine.setup", "run"], cwd=str(config.REPO), env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                bufsize=1, creationflags=config.NO_WINDOW)
        _state["proc"] = proc
    threading.Thread(target=_follow, args=(proc,), daemon=True).start()
    return status()


def _follow(proc: subprocess.Popen):
    for line in proc.stdout:
        line = line.strip()
        try:
            ev = json.loads(line)
        except ValueError:
            with _state_lock:
                _state["log"] = (_state["log"] + [line])[-40:]
            continue
        with _state_lock:
            if ev.get("error"):
                _state["error"] = ev["error"]
            if ev.get("id"):
                _state["progress"][ev["id"]] = ev
        if ev.get("id") == "ollama" and ev.get("state") == "done":
            ollama_host.start()  # the next downloads go through it
    proc.wait()
    with _state_lock:
        if proc.returncode and not _state["error"]:
            _state["error"] = "Setup stopped: " + (" ".join(_state["log"][-3:]) or f"exit code {proc.returncode}")


# the download process -----------------------------------------------------------------------------------------------
def _say(**ev):
    print(json.dumps(ev), flush=True)


def _fetch(url: str, dest: Path, sha256: str, item: str, total: int):
    """Downloads with resume (a .part file) and checks the SHA-256 before the file is used."""
    import httpx
    part = dest.with_name(dest.name + ".part")
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and _sha(dest) == sha256:
        return
    for attempt in range(6):
        have = part.stat().st_size if part.exists() else 0
        headers = {"Range": f"bytes={have}-"} if have else {}
        try:
            with httpx.stream("GET", url, headers=headers, follow_redirects=True, timeout=httpx.Timeout(60, connect=20)) as r:
                if r.status_code == 416:  # already complete
                    break
                r.raise_for_status()
                mode = "ab" if have and r.status_code == 206 else "wb"
                got = have if mode == "ab" else 0
                total = int(r.headers.get("content-length", 0)) + got or total
                last = 0.0
                with open(part, mode) as f:
                    for chunk in r.iter_bytes(1 << 20):
                        f.write(chunk)
                        got += len(chunk)
                        if time.time() - last > 0.5:
                            _say(id=item, state="downloading", got=got, total=total)
                            last = time.time()
            break
        except Exception as e:
            if attempt == 5:
                raise RuntimeError(f"Download failed ({e}). Check the internet connection and try again.") from None
            time.sleep(3 * (attempt + 1))
    _say(id=item, state="checking")
    if _sha(part) != sha256:
        part.unlink(missing_ok=True)
        raise RuntimeError("The downloaded model server did not match its published checksum, so it was not used.")
    part.replace(dest)


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _install_ollama():
    name, sha, size = OLLAMA_FILES[sys.platform]
    archive = config.DATA_DIR / "downloads" / name
    _fetch(f"https://github.com/ollama/ollama/releases/download/{OLLAMA_VERSION}/{name}", archive, sha, "ollama", size)
    _say(id="ollama", state="unpacking")
    dest = ollama_host.binary().parent
    tmp = dest.with_name(dest.name + ".new")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    if name.endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            z.extractall(tmp)
    else:
        with tarfile.open(archive) as t:
            t.extractall(tmp, filter="data")
    exe = next((p for p in tmp.rglob(ollama_host.binary().name) if p.is_file()), None)
    if not exe:
        raise RuntimeError("The model server archive did not contain the program.")
    root = exe.parent
    shutil.rmtree(dest, ignore_errors=True)
    shutil.move(str(root), str(dest))
    shutil.rmtree(tmp, ignore_errors=True)
    if not config.WINDOWS:
        ollama_host.binary().chmod(0o755)
    archive.unlink(missing_ok=True)
    _say(id="ollama", state="done")


def _pull(model: str, item: str, alias: str | None = None):
    """Pulls through the bundled Ollama (it verifies every layer) and makes the 8K-context alias Vyum uses."""
    import httpx
    base = f"http://127.0.0.1:{ollama_host.port()}"
    for _ in range(90):  # the engine starts the server once it is unpacked
        try:
            httpx.get(base + "/api/version", timeout=2)
            break
        except Exception:
            time.sleep(1)
    else:
        raise RuntimeError("The model server did not start. See ollama.log in Vyum's data folder.")
    for attempt in range(4):
        try:
            with httpx.stream("POST", base + "/api/pull", json={"model": model, "stream": True}, timeout=None) as r:
                r.raise_for_status()
                layers: dict[str, tuple[int, int]] = {}
                last = 0.0
                for line in r.iter_lines():
                    if not line:
                        continue
                    ev = json.loads(line)
                    if ev.get("error"):
                        raise RuntimeError(ev["error"])
                    if ev.get("digest") and ev.get("total"):
                        layers[ev["digest"]] = (ev.get("completed", 0), ev["total"])
                        if time.time() - last > 0.5:
                            _say(id=item, state="downloading", got=sum(c for c, _ in layers.values()),
                                 total=sum(t for _, t in layers.values()))
                            last = time.time()
            break
        except Exception as e:
            if attempt == 3:
                raise RuntimeError(f"Could not download {model} ({e}). Check the internet connection and try again.") from None
            time.sleep(5 * (attempt + 1))
    if alias:
        _say(id=item, state="preparing")
        r = httpx.post(base + "/api/create", json={"model": alias, "from": model, "parameters": {"num_ctx": 8192},
                                                   "stream": False}, timeout=600)
        r.raise_for_status()
    _say(id=item, state="done")


def _grow(item: str, folder: Path, total: int, work):
    """Runs `work` while reporting how much has arrived in `folder` (for libraries without byte progress)."""
    done = threading.Event()

    def watch():
        while not done.wait(1):
            got = sum(f.stat().st_size for f in folder.rglob("*") if f.is_file()) if folder.exists() else 0
            _say(id=item, state="downloading", got=min(got, total), total=total)
    threading.Thread(target=watch, daemon=True).start()
    try:
        work()
    finally:
        done.set()
    _say(id=item, state="done")


def run():
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    todo = {i["id"]: i for i in items() if not i["done"]}
    try:
        if "ollama" in todo:
            _install_ollama()
        # model pulls go through the engine's Ollama (it starts it once the program is unpacked; see _follow)
        if "gen" in todo:
            model, alias, _ = TIERS[tier()]
            _pull(model, "gen", alias)
        if "embed" in todo:
            _pull(EMBED[0], "embed")
        if "reranker" in todo:
            from huggingface_hub import snapshot_download
            hub = Path(os.environ.get("HF_HOME", config.MODELS_DIR / "hf")) / "hub"
            _grow("reranker", hub / ("models--" + config.RERANK_MODEL.replace("/", "--")), RERANKER_BYTES,
                  lambda: snapshot_download(config.RERANK_MODEL, allow_patterns=["*.json", "*.safetensors", "*.model",
                                                                                 "*.txt", "sentencepiece*"]))
        if "docling" in todo:
            from docling.utils.model_downloader import download_models
            _grow("docling", config.DOCLING_MODELS, DOCLING_BYTES,
                  lambda: download_models(output_dir=config.DOCLING_MODELS, with_layout=True, with_tableformer=True,
                                          with_code_formula=False, with_picture_classifier=False, with_rapidocr=True))
        if "vision" in todo:
            _pull(VISION[0], "vision", VISION[1])
    except Exception as e:
        _say(error=str(e))
        sys.exit(1)
    _say(state="finished")


if __name__ == "__main__" and sys.argv[1:] == ["run"]:
    run()
