"""Entry point. The shell spawns `familiar-brain --token <secret>` and reads `READY <port>` from stdout."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import socket
import sys
import threading
from logging.handlers import RotatingFileHandler

import uvicorn

from .config import Paths, Settings
from .server import Brain, create_app


def _watch_stdin() -> None:
    """Exit when the parent closes our stdin (shell died or asked us to stop)."""
    try:
        for _ in sys.stdin:
            pass
    except Exception:
        pass
    os._exit(0)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="familiar-brain")
    ap.add_argument("--token", default=os.environ.get("FAMILIAR_TOKEN", ""))
    ap.add_argument("--port", type=int, default=0, help="0 = pick a free port")
    ap.add_argument("--home", default=None, help="data dir (default %%LOCALAPPDATA%%\\Familiar)")
    ap.add_argument("--dev", action="store_true", help="verbose logging to stderr")
    ap.add_argument("--no-stdin-watch", action="store_true")
    a = ap.parse_args(argv)
    if not a.token:
        print("refusing to start without --token (or FAMILIAR_TOKEN)", file=sys.stderr)
        sys.exit(2)

    paths = (Paths.default() if not a.home else Paths(__import__("pathlib").Path(a.home))).ensure()
    handlers: list[logging.Handler] = [RotatingFileHandler(paths.logs / "brain.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")]
    if a.dev or not getattr(sys, "frozen", False):
        handlers.append(logging.StreamHandler(sys.stderr))
    logging.basicConfig(level=logging.DEBUG if a.dev else logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    settings = Settings.load(paths.settings_file)
    brain = Brain(paths, settings)
    port = a.port
    if port == 0:
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
    if not a.no_stdin_watch:
        threading.Thread(target=_watch_stdin, daemon=True).start()

    app = create_app(brain, a.token)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", ws="websockets"))

    async def serve() -> None:
        task = asyncio.create_task(server.serve())
        # Announce only once the socket is actually accepting connections (the ASGI startup event fires earlier).
        while not server.started and not task.done():
            await asyncio.sleep(0.02)
        if not task.done():
            print(f"READY {port}", flush=True)
        await task

    asyncio.run(serve())


if __name__ == "__main__":
    main()
