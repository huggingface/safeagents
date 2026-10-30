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

from .constants import METHODS, SKIP_REQUEST, SKIP_RESPONSE
from .guard import Config, Guard
from .rewrite import SseRewriter, guard_response
from .utils import compact_json

logger = logging.getLogger("safeagents")


def create_app(upstream: str, config: Config, transport: httpx.AsyncBaseTransport | None = None) -> Starlette:
    upstream = upstream.rstrip("/")
    # No read timeout: streams can idle for a long time between events.
    client = httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(None, connect=10.0))

    # Single catch-all handler: forwards the request upstream and guards the response.
    async def forward(request: Request) -> Response:
        # Read the whole body up front: we need it both to parse it and to forward it.
        body = await request.body()
        try:
            request_json = json.loads(body) if body else None
        except ValueError:
            request_json = None

        # Only JSON object bodies are model calls worth guarding.
        guard = None
        if isinstance(request_json, dict):
            guard = Guard(request_json, config)
            # `count_tokens` also carries `messages` but doesn't produce a model turn.
            is_model_turn = "messages" in request_json and not request.url.path.endswith("/count_tokens")
            # here we increment the counter of the number of requests from the model
            # it is activated only when monitor is turned on
            if config.monitor and is_model_turn:
                config.monitor.request()

        # Same path and query string as the incoming request, on the upstream host.
        url = upstream + request.url.path
        if request.url.query:
            url += f"?{request.url.query}"

        # Here we build the request that is going to be sent to the upstream request URL
        # we preserve its content and skip the meta-data from SKIP_REQUEST
        headers = [(name, value) for name, value in request.headers.raw if name.lower() not in SKIP_REQUEST]
        headers.append((b"accept-encoding", b"identity"))
        upstream_request = client.build_request(request.method, url, headers=headers, content=body)

        # `stream=True` only reads the headers, so we can decide below how to consume the body.
        # If upstream is unreachable, answer with a 502 instead of crashing the handler.
        try:
            upstream_response = await client.send(upstream_request, stream=True)
        except httpx.HTTPError as e:
            logger.warning("[safeagents] upstream error: %s", e)
            return PlainTextResponse(f"safeagents: upstream error: {e}", status_code=502)

        status_code = upstream_response.status_code
        response_headers = {
            name: value for name, value in upstream_response.headers.items() if name.lower() not in SKIP_RESPONSE
        }
        # Streaming responses outlive this handler, so the upstream connection is
        # closed once the client has received everything.
        close_upstream = BackgroundTask(upstream_response.aclose)

        # Not a model call: pass the response through untouched.
        if guard is None:
            return StreamingResponse(
                upstream_response.aiter_bytes(), status_code, response_headers, background=close_upstream
            )
        # Streamed model response (SSE): guard it event by event as it arrives.
        if upstream_response.headers.get("content-type", "").startswith("text/event-stream"):
            return StreamingResponse(
                _guarded(upstream_response, guard), status_code, response_headers, background=close_upstream
            )

        # Non-streamed model response: read it fully, guard it, and only re-serialize
        # when the guard actually changed something, so untouched bodies stay byte-identical.
        data = await upstream_response.aread()
        await upstream_response.aclose()
        try:
            response_json = json.loads(data)
        except ValueError:
            response_json = None
        if isinstance(response_json, dict):
            has_been_guarded = await guard_response(response_json, guard)
            if has_been_guarded:
                data = compact_json(response_json).encode()
        return Response(data, status_code, response_headers)

    # Ties the shared httpx client to the app's lifetime so it is closed on shutdown.
    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        async with client:
            yield

    # A single catch-all route: every path and method goes through `forward`.
    return Starlette(routes=[Route("/{path:path}", forward, methods=METHODS)], lifespan=lifespan)


async def _guarded(upstream_response: httpx.Response, guard: Guard) -> AsyncIterator[bytes]:
    """Yields the upstream SSE stream after running each event through the guard."""
    rewriter = SseRewriter(guard)
    # Chunks don't line up with event boundaries: the rewriter buffers partial events
    # and only returns bytes that are safe to send.
    async for chunk in upstream_response.aiter_bytes():
        out = await rewriter.feed(chunk)
        if out:
            yield out
    # Flush any trailing event and whatever the guard was still holding back.
    out = await rewriter.finish()
    if out:
        yield out
