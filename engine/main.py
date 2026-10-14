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
