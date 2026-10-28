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

log = logging.getLogger("safeagents")

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
        parts = (p.get("text") for p in content if isinstance(p, dict) and p.get("type") in ("text", "input_text"))
        return "\n".join(p for p in parts if isinstance(p, str))
    return ""


def _arguments(raw: object) -> dict:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return {"input": raw}
    return raw if isinstance(raw, dict) else {"input": json.dumps(raw)}


def _call(call_id: object, name: object, args: object) -> dict:
    return {"tool_call_id": str(call_id), "function_name": str(name), "arguments": _arguments(args)}


def call_step(calls: list[tuple[str, str]]) -> dict:
    """The agent step holding a response's pending calls."""
    return {
        "source": "agent",
        "message": "",
        "tool_calls": [_call(f"pending-{i}", name, args) for i, (name, args) in enumerate(calls)],
    }


def history(request: dict) -> list[dict]:
    """ATIF steps for an OpenAI or Anthropic request's conversation, without reasoning."""
    steps: list[dict] = []

    def observe(call_id: object, content: object) -> None:
        agent = next((s for s in reversed(steps) if s["source"] == "agent"), None)
        if agent is None or not agent.get("tool_calls"):
            return
        results = agent.setdefault("observation", {"results": []})["results"]
        results.append({"source_call_id": str(call_id), "content": _text(content)})

    if system := _text(request.get("system")):
        steps.append({"source": "system", "message": system})
    for m in request.get("messages") or []:
        if not isinstance(m, dict):
            continue
        role, content = m.get("role"), m.get("content")
        if role in ("system", "developer"):
            steps.append({"source": "system", "message": _text(content)})
        elif role == "user":
            blocks = content if isinstance(content, list) else []
            for b in blocks:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    observe(b.get("tool_use_id"), b.get("content"))
            if text := _text(content):
                steps.append({"source": "user", "message": text})
        elif role == "assistant":
            calls = []
            for tc in m.get("tool_calls") or []:  # OpenAI
                if isinstance(tc, dict) and isinstance(tc.get("function"), dict):
                    f = tc["function"]
                    calls.append(_call(tc.get("id"), f.get("name"), f.get("arguments")))
            for b in content if isinstance(content, list) else []:  # Anthropic
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    calls.append(_call(b.get("id"), b.get("name"), b.get("input")))
            step = {"source": "agent", "message": _text(content)}
            if calls:
                step["tool_calls"] = calls
            steps.append(step)
        elif role == "tool":
            observe(m.get("tool_call_id"), content)
    return steps


def recent(steps: list[dict], agent_steps: int = 20) -> list[dict]:
    """`steps` with only the last `agent_steps` agent turns. Scan time grows with the
    trace and only findings on the pending calls count, but what the user asked for
    can excuse them (atif-scan's allowances), so user and system turns all stay."""
    agents = [i for i, s in enumerate(steps) if s["source"] == "agent"]
    cut = set(agents[:-agent_steps]) if len(agents) > agent_steps else set()
    return [s for i, s in enumerate(steps) if i not in cut]


class Scanner:
    def __init__(self) -> None:
        self.engine = Engine([*builtin_detectors(), *access_rules(), *builtin_allowances(), *EXTRA_DETECTORS])

    def signals(self, steps: list[dict], calls: list[tuple[str, str]]) -> list[Signal]:
        """Findings on the last of `calls`, judged after the conversation `steps`."""
        trace_steps = [*steps, call_step(calls)]
        last_step, last_call = len(trace_steps) - 1, len(calls) - 1
        try:
            trace = parse_trace({
                "schema_version": "ATIF-v1.7",
                "steps": [{"step_id": i + 1, **s} for i, s in enumerate(trace_steps)],
            })
            results = self.engine.evaluate(trace, Context())
        except Exception as e:  # noqa: BLE001 - a scanner bug must not take the proxy down
            log.warning("[safeagents] atif-scan failed: %s", e)
            return []
        return [
            Signal(a.spec.id, a.spec.severity, a.spec.title)
            for a in results
            if a.counts
            and a.spec.severity > Severity.INFO
            and not a.spec.id.startswith(TRACE_QUALITY)
            and any(e.step == last_step and e.call == last_call for e in a.result.evidence)
        ]
