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
        _stop_leftover()
        log = open(config.DATA_DIR / "ollama.log", "ab")
        _proc = subprocess.Popen([str(binary()), "serve"], env=env(), stdout=log, stderr=log,
                                 creationflags=config.NO_WINDOW)
        _pid_file().write_text(str(_proc.pid))
        atexit.register(stop)
        return True


def _pid_file() -> Path:
    return config.DATA_DIR / "ollama.pid"


def _stop_leftover():
    """A previous engine that crashed or was killed left its model server running: stop it (only if it is ours)."""
    try:
        pid = int(_pid_file().read_text())
    except (OSError, ValueError):
        return
    try:
        if config.WINDOWS:
            out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True,
                                 text=True, creationflags=config.NO_WINDOW).stdout
            if "ollama" in out.lower():
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True,
                               creationflags=config.NO_WINDOW)
        else:
            cmd = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True).stdout
            if str(binary()) in cmd:
                os.kill(pid, 15)
    except Exception:
        pass
    _pid_file().unlink(missing_ok=True)


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
        _pid_file().unlink(missing_ok=True)
