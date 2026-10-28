"""Applies a Guard to OpenAI and Anthropic responses, full or streamed.

A blocked call is replaced by a text notice, and a "tool use" stop reason becomes a
plain stop when no call is left, so the harness sees a text reply and runs nothing.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .guard import Guard

TOOL_BLOCKS = ("tool_use", "server_tool_use")


def dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def append_content(slot: dict, text: str) -> None:
    """Appends `text` to an OpenAI `message` or `delta` object's `content`."""
    current = slot.get("content")
    slot["content"] = f"{current}\n{text}" if isinstance(current, str) and current else text


def swap_reason(slot: dict, key: str, old: str, new: str) -> bool:
    if slot.get(key) == old:
        slot[key] = new
        return True
    return False


async def guard_response(resp: dict, guard: Guard) -> bool:
    """Checks a full (non-streaming) response; True if it was changed."""
    before = len(guard.flagged)

    # OpenAI: choices[].message.tool_calls[]
    for choice in resp.get("choices") or []:
        msg = choice.get("message") if isinstance(choice, dict) else None
        if not isinstance(msg, dict) or not isinstance(msg.get("tool_calls"), list):
            continue
        seen = len(guard.flagged)
        kept = []
        for tc in msg["tool_calls"]:
            f = tc.get("function") or {}
            if await guard.allow(str(f.get("name") or ""), str(f.get("arguments") or "")):
                kept.append(tc)
        found = guard.flagged[seen:]
        if not guard.block or not found:
            continue
        if kept:
            msg["tool_calls"] = kept
        else:
            del msg["tool_calls"]
            swap_reason(choice, "finish_reason", "tool_calls", "stop")
        for b in found:
            append_content(msg, b.notice())

    # Anthropic: content[] tool_use blocks
    content = resp.get("content")
    if isinstance(content, list):
        seen = len(guard.flagged)
        kept_tool = False
        for i, b in enumerate(content):
            if not (isinstance(b, dict) and b.get("type") in TOOL_BLOCKS):
                continue
            if await guard.allow(str(b.get("name") or ""), dumps(b.get("input"))):
                kept_tool = True
            elif guard.block:
                content[i] = {"type": "text", "text": guard.flagged[-1].notice()}
        if guard.block and len(guard.flagged) > seen and not kept_tool:
            swap_reason(resp, "stop_reason", "tool_use", "end_turn")

    return guard.block and len(guard.flagged) > before


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
        ok = await self.guard.allow(name, args)
        self.kept_tool |= ok
        return ok

    def _swap_reason(self) -> bool:
        return self.guard.block and bool(self.guard.flagged) and not self.kept_tool

    async def event(self, ev: object, raw: bytes) -> Out:
        if not isinstance(ev, dict):
            return None
        kind = ev.get("type")
        holding = self.held is not None and self.held.index == ev.get("index")
        if kind == "content_block_start" and (ev.get("content_block") or {}).get("type") in TOOL_BLOCKS:
            block = ev["content_block"]
            start = block.get("input")
            self.held = _HeldBlock(ev.get("index"), str(block.get("name") or ""), dumps(start) if start else "", [raw])
            return []
        if kind == "content_block_delta" and holding:
            part = (ev.get("delta") or {}).get("partial_json")
            if isinstance(part, str):
                self.held.input += part
            self.held.raw.append(raw)
            return []
        if kind == "content_block_stop" and holding:
            held, self.held = self.held, None
            held.raw.append(raw)
            return await self._release_block(held)
        if kind == "message_delta":
            delta = ev.get("delta")
            if self._swap_reason() and isinstance(delta, dict) and swap_reason(delta, "stop_reason", "tool_use", "end_turn"):
                return [ev]
            return None
        if isinstance(ev.get("choices"), list):
            return await self._openai_chunk(ev)
        return None

    async def _release_block(self, held: _HeldBlock) -> list[bytes | dict]:
        if await self._allow(held.name, held.input):
            return list(held.raw)
        # Keep the block index so the stream's block numbering stays intact.
        text = {"type": "text", "text": self.guard.flagged[-1].notice()}
        return [
            {"type": "content_block_start", "index": held.index, "content_block": text},
            {"type": "content_block_stop", "index": held.index},
        ]

    async def _openai_chunk(self, ev: dict) -> Out:
        changed = False
        finished = []
        for choice in ev["choices"]:
            if not isinstance(choice, dict):
                continue
            ci = choice.get("index") or 0
            delta = choice.get("delta")
            if isinstance(delta, dict) and "tool_calls" in delta:
                changed = True
                for tc in delta.pop("tool_calls") or []:
                    self._absorb(ci, tc)
            if isinstance(choice.get("finish_reason"), str):
                finished.append(ci)
        self.template = ev
        if not finished:
            return [ev] if changed else None

        out: list[bytes | dict] = [c for ci in finished if (c := await self._release_calls(ci))]
        if self._swap_reason():
            for choice in ev["choices"]:
                swap_reason(choice, "finish_reason", "tool_calls", "stop")
        return [*out, ev]

    def _absorb(self, choice: int, tc: dict) -> None:
        call = self.pending.setdefault((choice, tc.get("index") or 0), _PendingCall())
        if isinstance(tc.get("id"), str):
            call.id = tc["id"]
        f = tc.get("function") or {}
        if isinstance(f.get("name"), str):
            call.name += f["name"]
        if isinstance(f.get("arguments"), str):
            call.args += f["arguments"]

    async def _release_calls(self, choice: int) -> dict | None:
        """One chunk carrying the complete, allowed tool calls of `choice`."""
        calls = [self.pending.pop(k) for k in list(self.pending) if k[0] == choice]
        if not calls:
            return None
        kept, notices = [], []
        for call in calls:
            if await self._allow(call.name, call.args):
                kept.append(call)
            else:
                notices.append(self.guard.flagged[-1].notice())
        delta: dict = {}
        if kept:
            delta["tool_calls"] = [
                {"index": i, "id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.args}}
                for i, c in enumerate(kept)
            ]
        if notices:
            append_content(delta, "\n".join(notices))
        chunk = {k: v for k, v in self.template.items() if k != "usage"}
        chunk["choices"] = [{"index": choice, "delta": delta, "finish_reason": None}]
        return chunk

    async def flush(self) -> list[bytes | dict]:
        """Releases anything still held, e.g. when the stream ends early."""
        out = []
        if self.held is not None:
            held, self.held = self.held, None
            out += await self._release_block(held)
        for ci in dict.fromkeys(k[0] for k in self.pending):
            if chunk := await self._release_calls(ci):
                out.append(chunk)
        return out


_BOUNDARY = re.compile(rb"\r\n\r\n|\n\n")


def _encode(items: list[bytes | dict]) -> bytes:
    out = bytearray()
    for item in items:
        if isinstance(item, bytes):
            out += item
            continue
        # Anthropic streams name each event after its `type`.
        if isinstance(item.get("type"), str):
            out += f"event: {item['type']}\n".encode()
        out += f"data: {dumps(item)}\n\n".encode()
    return bytes(out)


class SseRewriter:
    """Splits a byte stream into server-sent events and runs each through a
    StreamGuard. Events the guard passes are forwarded byte for byte."""

    def __init__(self, guard: Guard) -> None:
        self.stream = StreamGuard(guard)
        self.buf = b""

    async def feed(self, chunk: bytes) -> bytes:
        """Feeds a chunk and returns the bytes to send downstream."""
        self.buf += chunk
        out = bytearray()
        while m := _BOUNDARY.search(self.buf):
            raw, self.buf = self.buf[: m.end()], self.buf[m.end() :]
            out += await self._rewrite(raw[: m.start()], raw)
        return bytes(out)

    async def finish(self) -> bytes:
        """Flushes a trailing unterminated event and anything still held."""
        rest, self.buf = self.buf, b""
        out = await self._rewrite(rest, rest + b"\n\n") if rest else b""
        return out + _encode(await self.stream.flush())

    async def _rewrite(self, body: bytes, raw: bytes) -> bytes:
        lines = body.decode("utf-8", "replace").splitlines()
        data = next((line[5:].lstrip() for line in lines if line.startswith("data:")), None)
        if data is None:
            return raw
        try:
            ev = json.loads(data)
        except ValueError:
            # Non-JSON data such as `[DONE]`: release held calls before it.
            return _encode(await self.stream.flush()) + raw
        out = await self.stream.event(ev, raw)
        return raw if out is None else _encode(out)
