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
"""Runs atif-scan's checks on the tool calls a response is about to make.

The request's conversation becomes an ATIF trace, the response's calls its last
agent step, and only findings pointing at those calls are kept. Reasoning is left
out: a model's account of what it is doing is not evidence of what it does.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from atif_scan import CheckSpec, Context, Engine, RegexDetector, Severity, builtin_detectors, parse_trace
from atif_scan.access import access_rules
from atif_scan.data.model import Channel
from atif_scan.detectors.priming import builtin_allowances

logger = logging.getLogger("safeagents")

# Behaviour from the Cohere report that atif-scan has no check for yet.
EXTRA_DETECTORS = (
    RegexDetector(
        CheckSpec("safeagents.namespace_escape", Severity.MEDIUM, title="Creates or enters Linux namespaces"),
        r"\b(?:unshare|nsenter)\b",
        frozenset({Channel.COMMAND}),
    ),
)


# Checks on how complete a recorded trace is. A pending call has no results yet by
# definition, so they say nothing about it.
TRACE_QUALITY = "integrity."


@dataclass(frozen=True)
class Signal:
    id: str
    severity: Severity
    title: str

    def __str__(self) -> str:
        return f"{self.id} ({self.severity.name.lower()})"


def _text(content: object) -> str:
    """Text of a message `content`: a string, or the text parts of a list."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if not isinstance(part, dict) or part.get("type") not in ("text", "input_text"):
                continue
            if isinstance(part.get("text"), str):
                parts.append(part["text"])
        return "\n".join(parts)
    return ""


def _tool_call(call_id: object, name: object, args: object) -> dict:
    """An ATIF tool call; `args` may be a JSON string or an already parsed value."""
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            args = {"input": args}
    if not isinstance(args, dict):
        args = {"input": json.dumps(args)}
    return {"tool_call_id": str(call_id), "function_name": str(name), "arguments": args}


def call_step(calls: list[tuple[str, str]]) -> dict:
    """The agent step holding a response's pending calls."""
    tool_calls = [_tool_call(f"pending-{index}", name, args) for index, (name, args) in enumerate(calls)]
    return {"source": "agent", "message": "", "tool_calls": tool_calls}


def history(request: dict) -> list[dict]:
    """ATIF steps for an OpenAI or Anthropic request's conversation, without reasoning."""
    steps: list[dict] = []

    def observe(call_id: object, content: object) -> None:
        agent_step = None
        for step in reversed(steps):
            if step["source"] == "agent":
                agent_step = step
                break
        if agent_step is None or not agent_step.get("tool_calls"):
            return
        results = agent_step.setdefault("observation", {"results": []})["results"]
        results.append({"source_call_id": str(call_id), "content": _text(content)})

    system = _text(request.get("system"))
    if system:
        steps.append({"source": "system", "message": system})
    for message in request.get("messages") or []:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        content = message.get("content")
        content_blocks = content if isinstance(content, list) else []
        if role in ("system", "developer"):
            steps.append({"source": "system", "message": _text(content)})
        elif role == "user":
            for block in content_blocks:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    observe(block.get("tool_use_id"), block.get("content"))
            text = _text(content)
            if text:
                steps.append({"source": "user", "message": text})
        elif role == "assistant":
            tool_calls = []
            # OpenAI
            for tool_call in message.get("tool_calls") or []:
                if isinstance(tool_call, dict) and isinstance(tool_call.get("function"), dict):
                    function = tool_call["function"]
                    tool_calls.append(_tool_call(tool_call.get("id"), function.get("name"), function.get("arguments")))
            # Anthropic
            for block in content_blocks:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    tool_calls.append(_tool_call(block.get("id"), block.get("name"), block.get("input")))
            step = {"source": "agent", "message": _text(content)}
            if tool_calls:
                step["tool_calls"] = tool_calls
            steps.append(step)
        elif role == "tool":
            observe(message.get("tool_call_id"), content)
    return steps


def recent(steps: list[dict], agent_steps: int = 20) -> list[dict]:
    """`steps` with only the last `agent_steps` agent turns. Scan time grows with the
    trace and only findings on the pending calls count, but what the user asked for
    can excuse them (atif-scan's allowances), so user and system turns all stay."""
    agent_indices = [index for index, step in enumerate(steps) if step["source"] == "agent"]
    dropped = set()
    if len(agent_indices) > agent_steps:
        dropped = set(agent_indices[:-agent_steps])
    return [step for index, step in enumerate(steps) if index not in dropped]


class Scanner:
    """Runs atif-scan's built-in checks plus `EXTRA_DETECTORS`."""

    def __init__(self) -> None:
        self.engine = Engine([*builtin_detectors(), *access_rules(), *builtin_allowances(), *EXTRA_DETECTORS])

    def signals(self, steps: list[dict], calls: list[tuple[str, str]]) -> list[Signal]:
        """Findings on the last of `calls`, judged after the conversation `steps`."""
        trace_steps = [*steps, call_step(calls)]
        last_step = len(trace_steps) - 1
        last_call = len(calls) - 1
        try:
            trace = parse_trace(
                {
                    "schema_version": "ATIF-v1.7",
                    "steps": [{"step_id": index + 1, **step} for index, step in enumerate(trace_steps)],
                }
            )
            results = self.engine.evaluate(trace, Context())
        except Exception as e:  # noqa: BLE001 - a scanner bug must not take the proxy down
            logger.warning("[safeagents] atif-scan failed: %s", e)
            return []
        signals = []
        for finding in results:
            spec = finding.spec
            if not finding.counts or spec.severity <= Severity.INFO or spec.id.startswith(TRACE_QUALITY):
                continue
            on_pending_call = any(
                evidence.step == last_step and evidence.call == last_call for evidence in finding.result.evidence
            )
            if on_pending_call:
                signals.append(Signal(spec.id, spec.severity, spec.title))
        return signals
