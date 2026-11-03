"""Who may talk to the local API.

Listening on 127.0.0.1 is not enough on its own:

* any web page the user visits can send a *simple* cross-origin POST to
  http://127.0.0.1:<port> -- the browser sends it, it just hides the reply;
* DNS rebinding lets a hostile site reach 127.0.0.1 under its own hostname.

So: the Host header must be ours (kills rebinding), and state-changing
requests that carry an Origin must come from our own page or our extension
(whose ID is pinned by the public key in product.toml). Requests without an
Origin (the second-launch notifier, curl) are local processes, which could
reach us anyway.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

Middleware = Callable[[Request, Callable[[Request], Awaitable[Response]]], Awaitable[Response]]


def local_only_guard(port: int, extension_origin: str) -> Middleware:
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    origins = {f"http://127.0.0.1:{port}", f"http://localhost:{port}", extension_origin}

    async def guard(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.headers.get("host") not in hosts:
            return JSONResponse({"error": "forbidden host"}, status_code=403)
        origin = request.headers.get("origin")
        if request.method not in SAFE_METHODS and origin is not None and origin not in origins:
            return JSONResponse({"error": "forbidden origin"}, status_code=403)
        return await call_next(request)

    return guard
