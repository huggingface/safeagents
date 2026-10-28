"""Reverse proxy that runs every model response through the guard."""

from __future__ import annotations

import contextlib
import json
import logging
from collections.abc import AsyncIterator

import httpx
from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response, StreamingResponse
from starlette.routing import Route

from .guard import Config, Guard
from .rewrite import SseRewriter, dumps, guard_response

log = logging.getLogger("safeagents")

# Request headers not forwarded upstream. `accept-encoding` is replaced by `identity`
# so responses come back uncompressed and can be inspected.
SKIP_REQUEST = {b"host", b"content-length", b"accept-encoding", b"connection", b"transfer-encoding"}
SKIP_RESPONSE = {"content-length", "connection", "transfer-encoding", "content-encoding"}
METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]


def create_app(upstream: str, config: Config, transport: httpx.AsyncBaseTransport | None = None) -> Starlette:
    upstream = upstream.rstrip("/")
    # No read timeout: streams can idle for a long time between events.
    client = httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(None, connect=10.0))

    async def forward(request: Request) -> Response:
        body = await request.body()
        try:
            parsed = json.loads(body) if body else None
        except ValueError:
            parsed = None
        # Only JSON object bodies are model calls worth guarding.
        guard = Guard(parsed, config) if isinstance(parsed, dict) else None
        if guard is not None and config.monitor and "messages" in parsed and not request.url.path.endswith("/count_tokens"):
            config.monitor.request()

        url = upstream + request.url.path + (f"?{request.url.query}" if request.url.query else "")
        headers = [(k, v) for k, v in request.headers.raw if k.lower() not in SKIP_REQUEST]
        headers.append((b"accept-encoding", b"identity"))
        try:
            resp = await client.send(client.build_request(request.method, url, headers=headers, content=body), stream=True)
        except httpx.HTTPError as e:
            log.warning("[safeagents] upstream error: %s", e)
            return PlainTextResponse(f"safeagents: upstream error: {e}", status_code=502)

        out_headers = {k: v for k, v in resp.headers.items() if k.lower() not in SKIP_RESPONSE}
        if guard is None:
            return StreamingResponse(resp.aiter_bytes(), resp.status_code, out_headers, background=BackgroundTask(resp.aclose))
        if resp.headers.get("content-type", "").startswith("text/event-stream"):
            return StreamingResponse(_guarded(resp, guard), resp.status_code, out_headers, background=BackgroundTask(resp.aclose))

        data = await resp.aread()
        await resp.aclose()
        try:
            parsed_resp = json.loads(data)
        except ValueError:
            parsed_resp = None
        if isinstance(parsed_resp, dict) and await guard_response(parsed_resp, guard):
            data = dumps(parsed_resp).encode()
        return Response(data, resp.status_code, out_headers)

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        async with client:
            yield

    return Starlette(routes=[Route("/{path:path}", forward, methods=METHODS)], lifespan=lifespan)


async def _guarded(resp: httpx.Response, guard: Guard) -> AsyncIterator[bytes]:
    rewriter = SseRewriter(guard)
    async for chunk in resp.aiter_bytes():
        if out := await rewriter.feed(chunk):
            yield out
    if out := await rewriter.finish():
        yield out
