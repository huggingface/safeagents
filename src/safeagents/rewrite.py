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
"""Applies a Guard to OpenAI and Anthropic responses, full or streamed.

A blocked call is replaced by a text notice, and a "tool use" stop reason becomes a
plain stop when no call is left, so the harness sees a text reply and runs nothing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from .constants import SSE_BOUNDARY, TOOL_BLOCKS
from .guard import Guard
from .utils import compact_json


def append_content(slot: dict, text: str) -> None:
    """Appends `text` to an OpenAI `message` or `delta` object's `content`."""
    current = slot.get("content")
    if isinstance(current, str) and current:
        slot["content"] = f"{current}\n{text}"
    else:
        slot["content"] = text


def swap_reason(slot: dict, key: str, old: str, new: str) -> bool:
    if slot.get(key) == old:
        slot[key] = new
        return True
    return False


async def guard_response(response: dict, guard: Guard) -> bool:
    """
    Checks a full (non-streaming) response; True if it was changed.
    Takes as input the response from the server and a `Guard` class. The
    response object is modified in-place in case of any flagged content.
    """
    block = guard.config.block
    flagged_before = len(guard.flagged)
    choices = response.get("choices", [])

    # OpenAI: choices[].message.tool_calls[]
    for choice in choices:
        if isinstance(choice, dict):
            message = choice.get("message")
        else:
            message = None

        # If the message is not a valid dict of the list of tool calls not valid
        # we drop.
        if not isinstance(message, dict) or not isinstance(message.get("tool_calls"), list):
            continue

        flagged_before_choice = len(guard.flagged)
        kept_calls = []

        for tool_call in message["tool_calls"]:
            function = tool_call.get("function", {})
            name = str(function.get("name", ""))
            args = str(function.get("arguments", ""))

            if await guard.allow(name, args):
                kept_calls.append(tool_call)
        newly_flagged = guard.flagged[flagged_before_choice:]

        if not block or not newly_flagged:
            continue
        if kept_calls:
            message["tool_calls"] = kept_calls
        else:
            del message["tool_calls"]
            swap_reason(choice, "finish_reason", "tool_calls", "stop")
        for blocked in newly_flagged:
            append_content(message, blocked.notice())

    # Anthropic: content[] tool_use blocks
    content = response.get("content")
    if isinstance(content, list):
        flagged_before_content = len(guard.flagged)
        kept_tool = False
        for index, content_block in enumerate(content):
            if not (isinstance(content_block, dict) and content_block.get("type") in TOOL_BLOCKS):
                continue
            name = str(content_block.get("name", ""))
            args = compact_json(content_block.get("input"))
            if await guard.allow(name, args):
                kept_tool = True
            elif block:
                content[index] = {"type": "text", "text": guard.flagged[-1].notice()}
        if block and len(guard.flagged) > flagged_before_content and not kept_tool:
            swap_reason(response, "stop_reason", "tool_use", "end_turn")

    return block and len(guard.flagged) > flagged_before


@dataclass
class _HeldBlock:
    """An Anthropic tool_use block held until its input is complete."""

    index: object
    name: str
    input: str
    raw: list[bytes]


@dataclass
class _PendingCall:
    """An OpenAI tool call being assembled from deltas."""

    id: object = None
    name: str = ""
    args: str = ""


# What to send downstream for one event: None forwards it unchanged, otherwise these
# items (raw event bytes or JSON events) replace it.
Out = list[bytes | dict] | None


@dataclass
class StreamGuard:
    """Per-response state for guarding a stream of events.

    Tool-call events are held until the call is complete, then released unchanged or
    replaced. Harnesses only run complete calls, so holding them costs the agent no time.
    """

    guard: Guard
    held: _HeldBlock | None = None
    pending: dict[tuple[int, int], _PendingCall] = field(default_factory=dict)
    # Last OpenAI chunk seen, the template for released tool calls.
    template: dict = field(default_factory=dict)
    kept_tool: bool = False

    async def _allow(self, name: str, args: str) -> bool:
        allowed = await self.guard.allow(name, args)
        self.kept_tool |= allowed
        return allowed

    async def event(self, event: object, raw: bytes) -> Out:
        if not isinstance(event, dict):
            return None
        kind = event.get("type")
        holding = self.held is not None and self.held.index == event.get("index")
        content_block = event.get("content_block") or {}
        if kind == "content_block_start" and content_block.get("type") in TOOL_BLOCKS:
            name = str(content_block.get("name") or "")
            start_input = content_block.get("input")
            input_text = ""
            if start_input:
                input_text = compact_json(start_input)
            self.held = _HeldBlock(event.get("index"), name, input_text, [raw])
            return []
        if kind == "content_block_delta" and holding:
            partial_json = (event.get("delta") or {}).get("partial_json")
            if isinstance(partial_json, str):
                self.held.input += partial_json
            self.held.raw.append(raw)
            return []
        if kind == "content_block_stop" and holding:
            held = self.held
            self.held = None
            held.raw.append(raw)
            return await self._release_block(held)
        if kind == "message_delta":
            delta = event.get("delta")
            # The stop reason only changes when every call so far was blocked.
            should_swap_reason = self.guard.config.block and self.guard.flagged and not self.kept_tool
            if (
                should_swap_reason
                and isinstance(delta, dict)
                and swap_reason(delta, "stop_reason", "tool_use", "end_turn")
            ):
                return [event]
            return None
        if isinstance(event.get("choices"), list):
            return await self._openai_chunk(event)
        return None

    async def _release_block(self, held: _HeldBlock) -> list[bytes | dict]:
        if await self._allow(held.name, held.input):
            return list(held.raw)
        # Keep the block index so the stream's block numbering stays intact. The notice
        # goes in a delta: clients such as Claude Code build text only from deltas.
        text_block = {"type": "text", "text": ""}
        text_delta = {"type": "text_delta", "text": self.guard.flagged[-1].notice()}
        return [
            {"type": "content_block_start", "index": held.index, "content_block": text_block},
            {"type": "content_block_delta", "index": held.index, "delta": text_delta},
            {"type": "content_block_stop", "index": held.index},
        ]

    async def _openai_chunk(self, event: dict) -> Out:
        changed = False
        finished_choices = []
        for choice in event["choices"]:
            if not isinstance(choice, dict):
                continue
            choice_index = choice.get("index") or 0
            delta = choice.get("delta")
            if isinstance(delta, dict) and "tool_calls" in delta:
                changed = True
                for tool_call in delta.pop("tool_calls") or []:
                    self._absorb(choice_index, tool_call)
            if isinstance(choice.get("finish_reason"), str):
                finished_choices.append(choice_index)
        self.template = event
        if not finished_choices:
            return [event] if changed else None

        out: list[bytes | dict] = []
        for choice_index in finished_choices:
            chunk = await self._release_calls(choice_index)
            if chunk:
                out.append(chunk)
        # The stop reason only changes when every call so far was blocked.
        if self.guard.config.block and self.guard.flagged and not self.kept_tool:
            for choice in event["choices"]:
                swap_reason(choice, "finish_reason", "tool_calls", "stop")
        out.append(event)
        return out

    def _absorb(self, choice_index: int, tool_call: dict) -> None:
        key = (choice_index, tool_call.get("index") or 0)
        call = self.pending.setdefault(key, _PendingCall())
        if isinstance(tool_call.get("id"), str):
            call.id = tool_call["id"]
        function = tool_call.get("function") or {}
        if isinstance(function.get("name"), str):
            call.name += function["name"]
        if isinstance(function.get("arguments"), str):
            call.args += function["arguments"]

    async def _release_calls(self, choice_index: int) -> dict | None:
        """One chunk carrying the complete, allowed tool calls of `choice_index`."""
        calls = []
        for key in list(self.pending):
            if key[0] == choice_index:
                calls.append(self.pending.pop(key))
        if not calls:
            return None
        kept_calls = []
        notices = []
        for call in calls:
            if await self._allow(call.name, call.args):
                kept_calls.append(call)
            else:
                notices.append(self.guard.flagged[-1].notice())
        delta: dict = {}
        if kept_calls:
            delta["tool_calls"] = [
                {
                    "index": index,
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": call.args},
                }
                for index, call in enumerate(kept_calls)
            ]
        if notices:
            append_content(delta, "\n".join(notices))
        chunk = {key: value for key, value in self.template.items() if key != "usage"}
        chunk["choices"] = [{"index": choice_index, "delta": delta, "finish_reason": None}]
        return chunk

    async def flush(self) -> list[bytes | dict]:
        """Releases anything still held, e.g. when the stream ends early."""
        out = []
        if self.held is not None:
            held = self.held
            self.held = None
            out += await self._release_block(held)
        pending_choices = dict.fromkeys(choice_index for choice_index, _ in self.pending)
        for choice_index in pending_choices:
            chunk = await self._release_calls(choice_index)
            if chunk:
                out.append(chunk)
        return out


def _encode(items: list[bytes | dict]) -> bytes:
    out = bytearray()
    for item in items:
        if isinstance(item, bytes):
            out += item
            continue
        # Anthropic streams name each event after its `type`.
        if isinstance(item.get("type"), str):
            out += f"event: {item['type']}\n".encode()
        data = compact_json(item)
        out += f"data: {data}\n\n".encode()
    return bytes(out)


class SseRewriter:
    """Splits a byte stream into server-sent events and runs each through a
    StreamGuard. Events the guard passes are forwarded byte for byte."""

    def __init__(self, guard: Guard) -> None:
        self.stream = StreamGuard(guard)
        self.buffer = b""

    async def feed(self, chunk: bytes) -> bytes:
        """Feeds a chunk and returns the bytes to send downstream."""
        self.buffer += chunk
        out = bytearray()
        while boundary := SSE_BOUNDARY.search(self.buffer):
            raw = self.buffer[: boundary.end()]
            self.buffer = self.buffer[boundary.end() :]
            out += await self._rewrite(raw[: boundary.start()], raw)
        return bytes(out)

    async def finish(self) -> bytes:
        """Flushes a trailing unterminated event and anything still held."""
        rest = self.buffer
        self.buffer = b""
        out = b""
        if rest:
            out = await self._rewrite(rest, rest + b"\n\n")
        return out + _encode(await self.stream.flush())

    async def _rewrite(self, body: bytes, raw: bytes) -> bytes:
        data = None
        for line in body.decode("utf-8", "replace").splitlines():
            if line.startswith("data:"):
                data = line[5:].lstrip()
                break
        if data is None:
            return raw
        try:
            event = json.loads(data)
        except ValueError:
            # Non-JSON data such as `[DONE]`: release held calls before it.
            return _encode(await self.stream.flush()) + raw
        out = await self.stream.event(event, raw)
        if out is None:
            return raw
        return _encode(out)
