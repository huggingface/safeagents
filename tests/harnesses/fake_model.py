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
"""A scripted model server for harness tests.

Speaks Anthropic Messages (`/v1/messages`) and OpenAI Chat Completions
(`/chat/completions`), streamed or not. The first user message picks what the
"model" does with a `SCENARIO=<name>` marker:

- `undeclared`: calls `safeagents_canary`, a tool no harness declares.
- `host`: calls `bash` with a URL that appears nowhere in the conversation.
- `clean`: calls `bash` with a harmless command.

Once the conversation holds a tool result, it answers with plain text.
"""

from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Self

CLEAN_OUTPUT = "safeagents-ok"
SCENARIOS = {
    "undeclared": ("safeagents_canary", {"command": "id"}),
    "host": ("bash", {"command": "echo https://exfil.invalid/probe"}),
    "clean": ("bash", {"command": f"echo {CLEAN_OUTPUT}"}),
}
FINAL_TEXT = "Done."
_SCENARIO = re.compile(r"SCENARIO=(\w+)")


def _message_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "\n".join(parts)
    return ""


def _scenario(messages: list) -> str:
    for message in messages:
        if message.get("role") != "user":
            continue
        match = _SCENARIO.search(_message_text(message.get("content")))
        if match:
            return match.group(1)
    return "clean"


def _has_tool_result(messages: list) -> bool:
    """True once the harness has sent back the result of a call (OpenAI or Anthropic)."""
    last = messages[-1] if messages else {}
    if last.get("role") == "tool":
        return True
    content = last.get("content")
    if last.get("role") == "user" and isinstance(content, list):
        return any(isinstance(block, dict) and block.get("type") == "tool_result" for block in content)
    return False


def _anthropic_events(tool: tuple[str, dict] | None) -> list[dict]:
    message = {
        "id": "msg_fake",
        "type": "message",
        "role": "assistant",
        "model": "fake-model",
        "content": [],
        "stop_reason": None,
        "stop_sequence": None,
        "usage": {"input_tokens": 1, "output_tokens": 0},
    }
    events = [{"type": "message_start", "message": message}]
    if tool is None:
        events += [
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": FINAL_TEXT}},
            {"type": "content_block_stop", "index": 0},
        ]
        stop_reason = "end_turn"
    else:
        name, arguments = tool
        tool_block = {"type": "tool_use", "id": "toolu_fake", "name": name, "input": {}}
        delta = {"type": "input_json_delta", "partial_json": json.dumps(arguments)}
        events += [
            {"type": "content_block_start", "index": 0, "content_block": tool_block},
            {"type": "content_block_delta", "index": 0, "delta": delta},
            {"type": "content_block_stop", "index": 0},
        ]
        stop_reason = "tool_use"
    events += [
        {
            "type": "message_delta",
            "delta": {"stop_reason": stop_reason, "stop_sequence": None},
            "usage": {"output_tokens": 1},
        },
        {"type": "message_stop"},
    ]
    return events


def _anthropic_message(tool: tuple[str, dict] | None) -> dict:
    if tool is None:
        content = [{"type": "text", "text": FINAL_TEXT}]
        stop_reason = "end_turn"
    else:
        name, arguments = tool
        content = [{"type": "tool_use", "id": "toolu_fake", "name": name, "input": arguments}]
        stop_reason = "tool_use"
    return {
        "id": "msg_fake",
        "type": "message",
        "role": "assistant",
        "model": "fake-model",
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }


def _openai_chunks(tool: tuple[str, dict] | None) -> list[dict]:
    if tool is None:
        delta = {"role": "assistant", "content": FINAL_TEXT}
        finish_reason = "stop"
    else:
        name, arguments = tool
        function = {"name": name, "arguments": json.dumps(arguments)}
        delta = {
            "role": "assistant",
            "tool_calls": [{"index": 0, "id": "call_fake", "type": "function", "function": function}],
        }
        finish_reason = "tool_calls"
    base = {"id": "chatcmpl-fake", "object": "chat.completion.chunk", "created": 0, "model": "fake-model"}
    usage = {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}
    return [
        {**base, "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
        {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}]},
        {**base, "choices": [], "usage": usage},
    ]


def _openai_completion(tool: tuple[str, dict] | None) -> dict:
    message: dict = {"role": "assistant", "content": None}
    if tool is None:
        message["content"] = FINAL_TEXT
        finish_reason = "stop"
    else:
        name, arguments = tool
        function = {"name": name, "arguments": json.dumps(arguments)}
        message["tool_calls"] = [{"id": "call_fake", "type": "function", "function": function}]
        finish_reason = "tool_calls"
    return {
        "id": "chatcmpl-fake",
        "object": "chat.completion",
        "created": 0,
        "model": "fake-model",
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


class FakeModel:
    """Runs the scripted server on a free local port and records the requests it gets."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, dict]] = []
        handler = type("Handler", (_Handler,), {"model": self})
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> Self:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.server.shutdown()
        self.server.server_close()


class _Handler(BaseHTTPRequestHandler):
    model: FakeModel

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        path = self.path.split("?", 1)[0]
        self.model.requests.append((path, body))

        messages = body.get("messages") or []
        tool = None
        if not _has_tool_result(messages):
            tool = SCENARIOS[_scenario(messages)]
        stream = bool(body.get("stream"))
        if path.endswith("/v1/messages"):
            if stream:
                self._send_events(_anthropic_events(tool), named=True)
            else:
                self._send_json(_anthropic_message(tool))
        elif path.endswith("/chat/completions"):
            if stream:
                self._send_events(_openai_chunks(tool), named=False)
            else:
                self._send_json(_openai_completion(tool))
        else:
            self.send_error(404)

    def do_GET(self) -> None:
        self.model.requests.append((self.path, {}))
        self.send_error(404)

    def _send_json(self, payload: dict) -> None:
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_events(self, events: list[dict], named: bool) -> None:
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("cache-control", "no-cache")
        self.end_headers()
        for event in events:
            # Anthropic streams name each event after its `type`; OpenAI ones don't.
            if named:
                self.wfile.write(f"event: {event['type']}\n".encode())
            self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
        if not named:
            self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()
        self.close_connection = True

    def log_message(self, *args: object) -> None:
        pass
