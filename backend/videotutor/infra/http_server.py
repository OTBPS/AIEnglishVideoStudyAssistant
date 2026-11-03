"""uvicorn in a background thread, on a socket we bound ourselves.

Binding first and handing the socket to uvicorn closes the race the first
draft had (probe a port, release it, let uvicorn bind it again -- another
program could take it in between).
"""

from __future__ import annotations

import logging
import socket
import threading
import time
from collections.abc import Iterable

import uvicorn
from fastapi import FastAPI

log = logging.getLogger("http")

HOST = "127.0.0.1"


class NoFreePortError(RuntimeError):
    pass


def bind_first_free(ports: Iterable[int], host: str = HOST) -> socket.socket:
    tried: list[int] = []
    for port in ports:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            # Windows: stop another process from binding the same port on top of us.
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            sock.bind((host, port))
        except OSError:
            sock.close()
            tried.append(port)
            continue
        return sock
    raise NoFreePortError(f"no free port among {tried}")


class HttpServer:
    def __init__(self, app: FastAPI, sock: socket.socket) -> None:
        self._sock = sock
        self.port: int = sock.getsockname()[1]
        config = uvicorn.Config(
            app, log_config=None, access_log=False, lifespan="off", timeout_graceful_shutdown=3
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._run, name="http", daemon=True)

    @property
    def url(self) -> str:
        return f"http://{HOST}:{self.port}/"

    def _run(self) -> None:
        try:
            self._server.run(sockets=[self._sock])
        except Exception:
            log.exception("HTTP server crashed")

    def start(self, timeout: float = 10.0) -> None:
        self._thread.start()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._server.started:
                log.info("listening on %s", self.url)
                return
            if not self._thread.is_alive():
                break
            time.sleep(0.02)
        raise RuntimeError(f"HTTP server did not start on port {self.port}")

    def stop(self, timeout: float = 5.0) -> None:
        self._server.should_exit = True
        if self._thread.is_alive():
            self._thread.join(timeout=timeout)
        self._sock.close()
