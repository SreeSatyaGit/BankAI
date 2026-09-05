"""Run the mock bank in a background thread so the CLI can drive it in-process
without managing a separate server subprocess."""
from __future__ import annotations

import threading
import time
import requests
from werkzeug.serving import make_server

from .app import create_app

HOST = "127.0.0.1"
PORT = 5001
BASE_URL = f"http://{HOST}:{PORT}"


class _ServerThread(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.srv = make_server(HOST, PORT, create_app())
        self.ctx = self.srv.app.app_context()
        self.ctx.push()

    def run(self):
        self.srv.serve_forever()

    def stop(self):
        self.srv.shutdown()


def start() -> _ServerThread:
    t = _ServerThread()
    t.start()
    # wait until it answers
    for _ in range(50):
        try:
            requests.get(BASE_URL + "/login", timeout=0.5)
            break
        except requests.RequestException:
            time.sleep(0.05)
    return t
