"""Quiet backend process owned by the Windows desktop window."""

import json
import os
import socket
import sys
import threading
import time

import uvicorn

from . import workspace


def run():
    workspace.prepare()
    from .app import app

    port = int(os.environ.get("RESEARCH_HUB_DESKTOP_PORT", "18765"))
    instance = os.environ.get("RESEARCH_HUB_INSTANCE")
    if not instance:
        raise SystemExit("Start the desktop backend through ResearchHub.exe")

    server = uvicorn.Server(uvicorn.Config(
        app, host="127.0.0.1", port=port, log_config=None, log_level="warning", timeout_graceful_shutdown=3,
    ))
    with socket.socket() as listener:
        if sys.platform == "win32":
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        listener.bind(("127.0.0.1", port))
        listener.listen()

        def parent_closed():
            try:
                sys.stdin.buffer.read(1)
            finally:
                server.should_exit = True

        def announce_ready():
            while not (server.started or server.should_exit):
                time.sleep(0.025)
            if server.started:
                print(json.dumps({"instance": instance, "url": f"http://127.0.0.1:{port}/"}), flush=True)

        threading.Thread(target=parent_closed, daemon=True).start()
        threading.Thread(target=announce_ready, daemon=True).start()
        server.run(sockets=[listener])
