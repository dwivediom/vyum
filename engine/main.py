"""Entry point: python -m engine.main --port 0 (0 = pick a free port). Prints VYUM_READY <port> once serving."""
import argparse
import os
import socket


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=int(os.environ.get("VYUM_PORT", "0")))
    args = p.parse_args()
    port = args.port
    if port == 0:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]

    if os.environ.get("VYUM_RESOURCES"):
        # Installed app: its own Ollama on a private port (reserved now, started once it is installed) and every model
        # cache inside Vyum's data folder. Set before the engine's modules read their settings.
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            os.environ.setdefault("VYUM_OLLAMA_PORT", str(s.getsockname()[1]))
        os.environ.setdefault("VYUM_LLM_URL", f"http://127.0.0.1:{os.environ['VYUM_OLLAMA_PORT']}/v1")
        from . import config, ollama_host, setup
        os.environ.setdefault("HF_HOME", str(config.MODELS_DIR / "hf"))
        if "VYUM_GEN_MODEL" not in os.environ:
            config.GEN_MODEL = setup.gen_alias()  # Qwen3-8B with 16 GB of memory, Qwen3-4B with less
        if not setup.prefs().get("vision") and "VYUM_DESCRIBE_FIGURES" not in os.environ:
            config.DESCRIBE_FIGURES = False  # the optional picture reader was not installed
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        ollama_host.start()

    # Hugging Face libraries must never reach the network once models are on disk.
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

    import uvicorn
    from .api import app

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)

    orig_startup = server.startup

    async def startup(sockets=None):
        await orig_startup(sockets)
        print(f"VYUM_READY {port}", flush=True)

    server.startup = startup
    server.run()


if __name__ == "__main__":
    main()
