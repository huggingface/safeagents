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
(`/chat/completions`), streamed or not. Markers in the user's prompt, one per line
(see `scenario_prompt`), pick the tool call the "model" makes:

- `SCENARIO=undeclared`: calls `safeagents_canary`, a tool no harness declares.
- `SCENARIO=host`: calls the shell tool with a URL that appears nowhere in the conversation.
- `SCENARIO=clean`: calls the shell tool with a harmless command.

`TOOL=` and `ARG=` name the harness's shell tool and its command argument, and
`EXTRA=` adds fixed arguments as compact JSON. Requests without a marker or without
tools (titles, quota checks, ...) and requests that already carry a tool result get
a plain text answer.
"""

from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Self

CLEAN_OUTPUT = "safeagents-ok"
CANARY_TOOL = "safeagents_canary"
EXFIL_HOST = "exfil.invalid"
COMMANDS = {
    "undeclared": "id",
    "host": f"echo https://{EXFIL_HOST}/probe",
    "clean": f"echo {CLEAN_OUTPUT}",
}
FINAL_TEXT = "Done."
_MARKER = re.compile(r"^(SCENARIO|TOOL|ARG|EXTRA)=(\S+)", re.MULTILINE)
_MODEL_INFO = {"id": "fake-model", "object": "model", "created": 0, "owned_by": "fake", "type": "model"}


def scenario_prompt(scenario: str, tool: str = "bash", arg: str = "command", extra: dict | None = None) -> str:
    """The prompt a test gives the harness, telling the fake model which call to make."""
    lines = ["Run the scripted step.", f"SCENARIO={scenario}", f"TOOL={tool}", f"ARG={arg}"]
    if extra:
        lines.append("EXTRA=" + json.dumps(extra, separators=(",", ":")))
    return "\n".join(lines)


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


def _markers(messages: list) -> dict[str, str]:
    """The markers of the first user message that has any."""
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        markers = dict(_MARKER.findall(_message_text(message.get("content"))))
        if "SCENARIO" in markers:
            return markers
    return {}


def _tool_results(messages: list) -> list[str]:
    """The text of every tool result the harness sent back (OpenAI or Anthropic)."""
    results = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        if message.get("role") == "tool":
            results.append(_message_text(message.get("content")))
            continue
        content = message.get("content")
        if message.get("role") != "user" or not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                results.append(_message_text(block.get("content")))
    return results


def _scripted_call(body: dict) -> tuple[str, dict] | None:
    """The tool call to answer `body` with, or None to answer in text."""
    messages = body.get("messages") or []
    markers = _markers(messages)
    if not markers or not body.get("tools") or _tool_results(messages):
        return None
    scenario = markers["SCENARIO"]
    tool = CANARY_TOOL if scenario == "undeclared" else markers.get("TOOL", "bash")
    arguments = {markers.get("ARG", "command"): COMMANDS[scenario]}
    if "EXTRA" in markers:
        arguments.update(json.loads(markers["EXTRA"]))
    return tool, arguments


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

    def scripted_requests(self) -> list[dict]:
        """Model calls that carried a scenario marker and declared tools."""
        scripted = []
        for _, body in self.requests:
            if body.get("tools") and _markers(body.get("messages") or []):
                scripted.append(body)
        return scripted

    def tool_results(self) -> list[str]:
        """Every tool result the harness sent back, i.e. what actually ran."""
        results = []
        for body in self.scripted_requests():
            for result in _tool_results(body.get("messages") or []):
                if result not in results:
                    results.append(result)
        return results


class _Handler(BaseHTTPRequestHandler):
    model: FakeModel

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        path = self.path.split("?", 1)[0]
        self.model.requests.append((path, body))

        tool = _scripted_call(body)
        stream = bool(body.get("stream"))
        if path.endswith("/messages/count_tokens"):
            self._send_json({"input_tokens": 1})
        elif path.endswith("/messages"):
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
        path = self.path.split("?", 1)[0]
        self.model.requests.append((path, {}))
        if path.endswith("/models"):
            self._send_json({"object": "list", "data": [_MODEL_INFO], "has_more": False})
        else:
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
