"""The model server of an installed Vyum: its own copy of Ollama (MIT), downloaded on first launch into Vyum's data
folder and run on a private port with Vyum's own model folder, so it never mixes with another Ollama on the computer.
A source checkout uses whatever Ollama is already running (VYUM_LLM_URL)."""
import atexit
import os
import socket
import subprocess
import threading
from pathlib import Path

from . import config

_lock = threading.Lock()
_proc: subprocess.Popen | None = None


def binary() -> Path:
    """Where setup unpacks Ollama: DATA/ollama (macOS: the `ollama` file; Windows: ollama.exe and its lib folder)."""
    return config.DATA_DIR / "ollama" / ("ollama.exe" if config.WINDOWS else "ollama")


def models_dir() -> Path:
    return config.MODELS_DIR / "ollama"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def port() -> int:
    return int(os.environ["VYUM_OLLAMA_PORT"])


def env() -> dict:
    return {**os.environ, "OLLAMA_HOST": f"127.0.0.1:{port()}", "OLLAMA_MODELS": str(models_dir()),
            "OLLAMA_KEEP_ALIVE": "10m", "OLLAMA_NOHISTORY": "1"}


def running() -> bool:
    return _proc is not None and _proc.poll() is None


def start() -> bool:
    """Starts the bundled server if it is installed and not running. True when it is running."""
    global _proc
    if not config.PACKAGED:
        return True
    with _lock:
        if running():
            return True
        if not binary().exists():
            return False
        models_dir().mkdir(parents=True, exist_ok=True)
        log = open(config.DATA_DIR / "ollama.log", "ab")
        _proc = subprocess.Popen([str(binary()), "serve"], env=env(), stdout=log, stderr=log,
                                 creationflags=config.NO_WINDOW)
        atexit.register(stop)
        return True


def stop():
    global _proc
    with _lock:
        if _proc and _proc.poll() is None:
            _proc.terminate()
            try:
                _proc.wait(5)
            except subprocess.TimeoutExpired:
                _proc.kill()
        _proc = None
