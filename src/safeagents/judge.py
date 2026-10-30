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
"""A decision model asked about tool calls the scanner found suspicious.

It sees what the user asked for, the call and the scanner's findings, but never the
agent's reasoning or prose: an agent that wants a call to run would argue for it
there. It can only add blocks, never lift one, and answers in constrained JSON.
"""

from __future__ import annotations

import json

import httpx

from .constants import DEFAULT_JUDGE_TIMEOUT, JUDGE_SCHEMA, JUDGE_SYSTEM, MAX_ARGS, MAX_TASK
from .scan import Signal


class JudgeError(Exception):
    pass


def task_text(request: dict) -> str:
    """The text of user turns: no system prompt (long and generic in most harnesses),
    tool results or agent turns. Keeps the end when long, as the latest ask matters most."""
    parts = []
    for message in request.get("messages") or []:
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
                    parts.append(block["text"])
    text = "\n\n".join(parts)
    if len(text) > MAX_TASK:
        text = "[...]\n" + text[-MAX_TASK:]
    return text


def build_prompt(task: str, name: str, args: str, signals: list[Signal]) -> str:
    """The user message sent to the judge for one call."""
    if len(args) > MAX_ARGS:
        args = args[:MAX_ARGS] + " [...]"
    findings = "\n".join(f"- {signal.id} ({signal.severity.name.lower()}): {signal.title}" for signal in signals)
    return (
        f"<task>\n{task}\n</task>\n\n"
        f"<tool_call name={json.dumps(name)}>\n{args}\n</tool_call>\n\n"
        f"<scanner_findings>\n{findings}\n</scanner_findings>"
    )


class Judge:
    """Client for an OpenAI-compatible chat completions endpoint (llama.cpp, vLLM, ...)."""

    def __init__(
        self,
        url: str,
        model: str,
        api_key: str | None = None,
        timeout: float = DEFAULT_JUDGE_TIMEOUT,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.url = url.rstrip("/") + "/chat/completions"
        self.model = model
        self.headers = {"authorization": f"Bearer {api_key}"} if api_key else {}
        self.timeout = timeout
        self.client = client or httpx.AsyncClient()

    async def review(self, request: dict, name: str, args: str, signals: list[Signal]) -> tuple[bool, str]:
        """(block, reason) for one call. Raises JudgeError if no usable verdict comes back."""
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": JUDGE_SYSTEM},
                {"role": "user", "content": build_prompt(task_text(request), name, args, signals)},
            ],
            "temperature": 0,
            "max_tokens": 256,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "verdict", "strict": True, "schema": JUDGE_SCHEMA},
            },
        }
        try:
            response = await self.client.post(self.url, json=body, headers=self.headers, timeout=self.timeout)
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            # Tolerate code fences or stray text around the object.
            json_text = content[content.index("{") : content.rindex("}") + 1]
            answer = json.loads(json_text)
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as e:
            raise JudgeError(f"{type(e).__name__}: {e}") from e
        verdict = answer.get("verdict")
        reason = answer.get("reason")
        if verdict not in ("allow", "block"):
            raise JudgeError(f"unexpected verdict {verdict!r}")
        return verdict == "block", str(reason or "").strip()
