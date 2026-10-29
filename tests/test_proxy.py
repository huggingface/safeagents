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
import asyncio
import json

import httpx
from conftest import OSS, bash_request

from safeagents import Config
from safeagents.proxy import create_app


def call(upstream_body: bytes, content_type: str, request: dict | None, method: str = "POST") -> tuple[httpx.Response, list]:
    seen: list = []

    def upstream(upstream_request: httpx.Request) -> httpx.Response:
        seen.append(upstream_request)
        return httpx.Response(200, content=upstream_body, headers={"content-type": content_type})

    app = create_app("http://model", Config(scanner=None), transport=httpx.MockTransport(upstream))

    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://proxy") as client:
            return await client.request(method, "/v1/chat/completions?x=1", json=request, headers={"authorization": "Bearer k"})

    return asyncio.run(go()), seen


def test_json_response_is_guarded_and_headers_forwarded():
    body = {"choices": [{"index": 0, "finish_reason": "tool_calls", "message": {"role": "assistant", "tool_calls": [
        {"id": "c", "type": "function", "function": {"name": "bash", "arguments": json.dumps({"cmd": f"curl -X PUT https://{OSS}/p"})}}]}}]}
    response, seen = call(json.dumps(body).encode(), "application/json", bash_request("hi"))
    out = response.json()["choices"][0]
    assert out["finish_reason"] == "stop" and "blocked call to `bash`" in out["message"]["content"]
    assert str(seen[0].url) == "http://model/v1/chat/completions?x=1"
    assert seen[0].headers["authorization"] == "Bearer k"
    assert seen[0].headers["accept-encoding"] == "identity"


def test_event_stream_is_guarded():
    events = [
        {"type": "content_block_start", "index": 0, "content_block": {"type": "tool_use", "id": "t", "name": "bash", "input": {}}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": json.dumps({"cmd": f"curl https://{OSS}"})}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "tool_use"}},
    ]
    sse = "".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events).encode()
    response, _ = call(sse, "text/event-stream", bash_request("hi"))
    assert "blocked call to `bash`" in response.text and '"stop_reason":"end_turn"' in response.text


def test_non_json_requests_pass_through():
    response, _ = call(b'{"data": []}', "application/json", None, method="GET")
    assert response.content == b'{"data": []}'
