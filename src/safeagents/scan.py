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

import importlib.util
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from atif_scan import CheckSpec, Context, Engine, RegexDetector, Severity, builtin_detectors, parse_trace
from atif_scan.access import access_rules
from atif_scan.data.model import Channel
from atif_scan.detectors.priming import builtin_allowances

from .constants import ATIF_SCHEMA_VERSION, TRACE_QUALITY
from .utils import content_text

logger = logging.getLogger("safeagents")

# Behaviour from the Cohere report that atif-scan has no check for yet.
EXTRA_DETECTORS = (
    RegexDetector(
        CheckSpec("safeagents.namespace_escape", Severity.MEDIUM, title="Creates or enters Linux namespaces"),
        r"\b(?:unshare|nsenter)\b",
        frozenset({Channel.COMMAND}),
    ),
)


@dataclass(frozen=True)
class Signal:
    id: str
    severity: Severity
    title: str

    def __str__(self) -> str:
        return f"{self.id} ({self.severity.name.lower()})"


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
        results.append({"source_call_id": str(call_id), "content": content_text(content)})

    system = content_text(request.get("system"))
    if system:
        steps.append({"source": "system", "message": system})
    for message in request.get("messages") or []:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        content = message.get("content")
        content_blocks = content if isinstance(content, list) else []
        if role in ("system", "developer"):
            steps.append({"source": "system", "message": content_text(content)})
        elif role == "user":
            for block in content_blocks:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    observe(block.get("tool_use_id"), block.get("content"))
            text = content_text(content)
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
            step = {"source": "agent", "message": content_text(content)}
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


HUB_PREFIX = "hf://"
HUB_REPO_TYPES = {"models": "model", "datasets": "dataset", "spaces": "space"}


def load_plugin(spec: str) -> list:
    """The checks `module:factory` returns, as atif-scan's `--plugin` takes them (e.g. `my_pack:checks`).

    The module may also be a file, `path/to/my_pack.py:checks`, or a file on the Hugging Face Hub,
    `hf://owner/repo` (see [`hub_plugin_file`]). A plugin returns atif-scan detectors and rules, which can add blocks,
    and allowances, which excuse the findings of the checks they cover (e.g. reading a file the task itself hands the
    agent).
    """
    if spec.startswith(HUB_PREFIX):
        path, factory = hub_plugin_file(spec)
        return load_file(path, factory)
    module, _, factory = spec.rpartition(":")
    if not module or not factory:
        raise ValueError(f"plugin must be MODULE:FACTORY, got {spec!r}")
    if module.endswith(".py"):
        return load_file(module, factory)
    return list(getattr(importlib.import_module(module), factory)())


def load_file(path: str, factory: str) -> list:
    """The checks `factory()` returns, from the Python file at `path`."""
    file_spec = importlib.util.spec_from_file_location(Path(path).stem, path)
    loaded = importlib.util.module_from_spec(file_spec)
    file_spec.loader.exec_module(loaded)
    return list(getattr(loaded, factory)())


def hub_plugin_file(spec: str) -> tuple[str, str]:
    """The local copy of a plugin on the Hugging Face Hub, and its factory: `hf://[TYPE/]OWNER/REPO[@REV][/FILE][:FACTORY]`.

    - `TYPE`: `datasets` or `spaces` for those repos; a model repo otherwise.
    - `REV`: a branch, tag or commit. Pin a commit: the file runs as code in this process.
    - `FILE`: the file in the repo; by default the only `.py` file at its root.
    - `FACTORY`: `checks` by default.

    `hf://harness-hub/qwen-safeagents-plugin` is the `checks` of the only Python file of that model repo, at its main
    branch. The file is downloaded once and cached (it also loads offline, with `HF_HUB_OFFLINE=1`).
    """
    from huggingface_hub import HfApi, hf_hub_download

    rest = spec[len(HUB_PREFIX) :]
    rest, _, factory = rest.rpartition(":") if ":" in rest else (rest, "", "checks")
    parts = rest.split("/")
    repo_type = HUB_REPO_TYPES.get(parts[0], "model")
    if parts[0] in HUB_REPO_TYPES:
        parts = parts[1:]
    if len(parts) < 2 or not all(parts[:2]) or not factory:
        raise ValueError(f"Hub plugin must be hf://[TYPE/]OWNER/REPO[@REV][/FILE][:FACTORY], got {spec!r}")
    name, _, revision = parts[1].partition("@")
    repo_id, filename = f"{parts[0]}/{name}", "/".join(parts[2:])
    if not filename:
        files = HfApi().list_repo_files(repo_id, repo_type=repo_type, revision=revision or None)
        candidates = [f for f in files if f.endswith(".py") and "/" not in f]
        if len(candidates) != 1:
            raise ValueError(
                f"{spec}: name the plugin file, e.g. hf://{repo_id}/FILE.py; Python files at the repo root: {candidates}"
            )
        filename = candidates[0]
    path = hf_hub_download(repo_id, filename, repo_type=repo_type, revision=revision or None)
    logger.info("[safeagents] plugin %s: %s", spec, path)
    return path, factory


class Scanner:
    """Runs atif-scan's built-in checks, `EXTRA_DETECTORS` and the `checks` plugins add."""

    def __init__(self, checks: Sequence = ()) -> None:
        self.engine = Engine([*builtin_detectors(), *access_rules(), *builtin_allowances(), *EXTRA_DETECTORS, *checks])

    def signals(self, steps: list[dict], calls: list[tuple[str, str]]) -> list[Signal]:
        """Findings on the last of `calls`, judged after the conversation `steps`."""
        trace_steps = [*steps, call_step(calls)]
        last_step = len(trace_steps) - 1
        last_call = len(calls) - 1
        try:
            trace = parse_trace(
                {
                    "schema_version": ATIF_SCHEMA_VERSION,
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
