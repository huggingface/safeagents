# Copyright 2026-present the HuggingFace Inc. team.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
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
from .rewrite import SseRewriter, guard_response

logger = logging.getLogger("safeagents")

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
            request_json = json.loads(body) if body else None
        except ValueError:
            request_json = None
        # Only JSON object bodies are model calls worth guarding.
        guard = None
        if isinstance(request_json, dict):
            guard = Guard(request_json, config)
            is_model_turn = "messages" in request_json and not request.url.path.endswith("/count_tokens")
            if config.monitor and is_model_turn:
                config.monitor.request()

        url = upstream + request.url.path
        if request.url.query:
            url += f"?{request.url.query}"
        headers = [(name, value) for name, value in request.headers.raw if name.lower() not in SKIP_REQUEST]
        headers.append((b"accept-encoding", b"identity"))
        upstream_request = client.build_request(request.method, url, headers=headers, content=body)
        try:
            upstream_response = await client.send(upstream_request, stream=True)
        except httpx.HTTPError as e:
            logger.warning("[safeagents] upstream error: %s", e)
            return PlainTextResponse(f"safeagents: upstream error: {e}", status_code=502)

        status_code = upstream_response.status_code
        response_headers = {
            name: value for name, value in upstream_response.headers.items() if name.lower() not in SKIP_RESPONSE
        }
        close_upstream = BackgroundTask(upstream_response.aclose)
        if guard is None:
            return StreamingResponse(
                upstream_response.aiter_bytes(), status_code, response_headers, background=close_upstream
            )
        if upstream_response.headers.get("content-type", "").startswith("text/event-stream"):
            return StreamingResponse(
                _guarded(upstream_response, guard), status_code, response_headers, background=close_upstream
            )

        data = await upstream_response.aread()
        await upstream_response.aclose()
        try:
            response_json = json.loads(data)
        except ValueError:
            response_json = None
        if isinstance(response_json, dict) and await guard_response(response_json, guard):
            data = json.dumps(response_json, ensure_ascii=False, separators=(",", ":")).encode()
        return Response(data, status_code, response_headers)

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        async with client:
            yield

    return Starlette(routes=[Route("/{path:path}", forward, methods=METHODS)], lifespan=lifespan)


async def _guarded(upstream_response: httpx.Response, guard: Guard) -> AsyncIterator[bytes]:
    rewriter = SseRewriter(guard)
    async for chunk in upstream_response.aiter_bytes():
        out = await rewriter.feed(chunk)
        if out:
            yield out
    out = await rewriter.finish()
    if out:
        yield out
