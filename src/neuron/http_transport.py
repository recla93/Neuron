"""Serve an MCP server over Streamable HTTP, using the SDK we already depend on.

WHY THIS EXISTS
---------------
The bridge used to shell out to `mcp-proxy` (via `uvx`), a separate project that
wraps a stdio server in an HTTP one. That worked until the MCP SDK moved: 1.28
dropped `request_ctx` from `mcp.server.lowlevel.server`, `mcp-proxy` still
imports it, and both bridges died on startup with an ImportError nobody saw —
`start` reported success and `stop` then said the process was gone.

That is not a one-off. A proxy pinned to a *different* release cycle than the
servers it proxies will drift again, and each drift takes down a feature that
looks unrelated. The SDK now ships `streamable_http_manager` itself, so the
transport can come from the same package as the protocol: one dependency, one
version, and a bump that breaks this breaks it visibly, at import, in our own
tests.

Requires `uvicorn` + `starlette`, which arrive with `mcp` — no `uvx`, no `uv`,
nothing to install separately.

keep-in-sync with `neurag/http_transport.py`.
"""
from __future__ import annotations

import contextlib
import os
import sys


def _bearer_token() -> str:
    return (os.environ.get("NEURON_BRIDGE_TOKEN") or "").strip()


def _refuse_open_bind(host: str) -> bool:
    """True when a non-loopback bind without a token must be refused.

    An MCP endpoint on 0.0.0.0 (or behind a tunnel) WITHOUT a shared secret
    exposes the whole MCP surface to whoever reaches it: refuse loudly at
    startup. NEURON_BRIDGE_ALLOW_OPEN=1 is the escape hatch for people who
    know what they are doing."""
    loopback = {"127.0.0.1", "localhost", "::1", ""}
    if host in loopback:
        return False
    if _bearer_token():
        return False
    return os.environ.get("NEURON_BRIDGE_ALLOW_OPEN", "").strip().lower() \
        not in ("1", "true", "yes")


def serve(app, host: str = "127.0.0.1", port: int = 8000,
          path: str = "/mcp", log_level: str = "info") -> None:
    """Serve `app` (an `mcp.server.lowlevel.Server`) over Streamable HTTP.

    Blocks until the server stops.

    `stateless=True` because the clients this is for — ChatGPT Dev Mode,
    Perplexity, anything behind a tunnel — reconnect freely and cannot be
    relied on to carry a session id. Stateless costs a little re-initialisation
    per request and removes a whole class of "works until the connection blips".

    Use `/mcp` (Streamable HTTP), not `/sse`: Cloudflare buffers the SSE
    handshake, which is the tunnel most of these setups sit behind.

    Auth: if `NEURON_BRIDGE_TOKEN` is set, every /mcp request must carry
    `Authorization: Bearer <token>`; a bind to a non-loopback host without a
    token is refused at startup (override: NEURON_BRIDGE_ALLOW_OPEN=1).
    """
    import hmac

    # The guard BEFORE any import: refusing an open bind must not depend on
    # uvicorn/mcp being importable.
    if _refuse_open_bind(host):
        print(
            f"neuron bridge: refusing to bind {host} without NEURON_BRIDGE_TOKEN "
            f"(an open MCP endpoint exposes destructive tools). Set the token, "
            f"or NEURON_BRIDGE_ALLOW_OPEN=1 to accept the risk.",
            file=sys.stderr)
        raise SystemExit(2)

    import uvicorn
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

    token = _bearer_token()
    manager = StreamableHTTPSessionManager(app=app, stateless=True)
    accepted = {path, path.rstrip("/") + "/"}

    def _authorized(headers) -> bool:
        if not token:
            return True
        auth = dict(headers).get(b"authorization", b"")
        expected = f"Bearer {token}".encode()
        return hmac.compare_digest(auth.strip(), expected)

    async def asgi(scope, receive, send):
        # Plain ASGI rather than Starlette routing. `Mount("/mcp")` answers
        # `/mcp` with a 307 to `/mcp/`, and a client that does not re-POST on
        # redirect — several do not, since a 307 with a body is exactly the case
        # HTTP libraries disagree about — sees the bridge as broken. Matching
        # both spellings here costs three lines and one dependency less.
        if scope["type"] == "lifespan":
            # The manager's task group must live for the whole server, not per
            # request: starting it per request is the shape that passes a smoke
            # test and deadlocks on the second client.
            async with manager.run():
                while True:
                    message = await receive()
                    if message["type"] == "lifespan.startup":
                        await send({"type": "lifespan.startup.complete"})
                    elif message["type"] == "lifespan.shutdown":
                        await send({"type": "lifespan.shutdown.complete"})
                        return
        elif scope["type"] == "http" and scope["path"] in accepted:
            if not _authorized(scope.get("headers", [])):
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"text/plain")]})
                await send({"type": "http.response.body", "body": b"unauthorized"})
                return
            await manager.handle_request(scope, receive, send)
        else:
            await send({"type": "http.response.start", "status": 404,
                        "headers": [(b"content-type", b"text/plain")]})
            await send({"type": "http.response.body",
                        "body": f"MCP is served at {path}".encode()})

    uvicorn.run(asgi, host=host, port=port, log_level=log_level)
