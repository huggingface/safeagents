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
import pytest
from atif_scan import Severity, parse_trace
from conftest import bash_request

from safeagents import Config, Guard
from safeagents.judge import Judge
from safeagents.scan import Scanner, call_step, history

SCANNER = Scanner()
ENVIRON = '{"command": "cat /proc/1/environ"}'


def ids(request: dict, args: str) -> set[str]:
    return {signal.id for signal in SCANNER.signals(history(request), [("bash", args)])}


def test_findings_on_the_pending_call():
    request = bash_request("Write a detection rule for curl downloads to /tmp.")
    assert "side_channel.harness_secret_read" in ids(request, ENVIRON)
    assert "network.proxy_bypass" in ids(request, '{"command": "curl --noproxy \'*\' https://example.com"}')
    assert "safeagents.namespace_escape" in ids(request, '{"command": "unshare -Urm sh"}')
    assert ids(request, '{"command": "ls -la && pytest -q"}') == set()


def test_trace_quality_checks_are_ignored():
    request = bash_request("hi")
    request["tools"].append({"type": "function", "function": {"name": "WebFetch"}})
    assert {signal.id for signal in SCANNER.signals(history(request), [("WebFetch", '{"url": "https://docs.python.org/3/"}')])} == set()


def test_findings_on_earlier_turns_are_ignored():
    request = bash_request("hi")
    request["messages"] += [
        {"role": "assistant", "tool_calls": [{"id": "a", "type": "function", "function": {"name": "bash", "arguments": ENVIRON}}]},
        {"role": "tool", "tool_call_id": "a", "content": "PATH=/bin"},
    ]
    assert ids(request, '{"command": "ls"}') == set()


ANTHROPIC = {
    "system": [{"type": "text", "text": "You are a coding agent."}],
    "messages": [
        {"role": "user", "content": "fix the tests"},
        {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "SECRET_REASONING"},
            {"type": "text", "text": "Running them."},
            {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "pytest"}},
        ]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": [{"type": "text", "text": "1 failed"}]}]},
    ],
    "tools": [{"name": "Bash", "input_schema": {}}],
}


def test_history_converts_both_formats():
    openai = bash_request("hi")
    openai["messages"] += [
        {"role": "assistant", "content": None, "reasoning_content": "SECRET_REASONING",
         "tool_calls": [{"id": "a", "type": "function", "function": {"name": "bash", "arguments": '{"command": "ls"}'}}]},
        {"role": "tool", "tool_call_id": "a", "content": "README.md"},
    ]
    for request in (openai, ANTHROPIC):
        steps = history(request)
        assert "SECRET_REASONING" not in json.dumps(steps)
        agent = next(step for step in steps if step.get("tool_calls"))
        assert agent["observation"]["results"][0]["source_call_id"] == agent["tool_calls"][0]["tool_call_id"]
        parse_trace({"schema_version": "ATIF-v1.7", "steps": [{"step_id": index + 1, **step} for index, step in enumerate([*steps, call_step([("bash", "{}")])])]})


def judge_returning(content: str | None, seen: list | None = None) -> Judge:
    def handler(judge_request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(json.loads(judge_request.content))
        if content is None:
            return httpx.Response(500)
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    return Judge("http://judge/v1", "j", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def review(config: Config, request: dict | None = None, args: str = ENVIRON) -> Guard:
    guard = Guard(request or bash_request("Write a detection rule for curl downloads to /tmp."), config)
    asyncio.run(guard.allow("bash", args))
    return guard


def test_suspicious_call_without_judge_is_only_logged(caplog):
    guard = review(Config(scanner=SCANNER))
    assert guard.flagged == []
    assert "suspicious call to `bash`" in caplog.text


def test_block_severity_threshold():
    guard = review(Config(scanner=SCANNER, block_severity=Severity.MEDIUM))
    assert guard.flagged and guard.flagged[0].reason.startswith("atif-scan: side_channel.harness_secret_read")


@pytest.mark.parametrize(
    ("answer", "fail_closed", "blocked"),
    [
        ('{"verdict": "block", "reason": "reads PID 1 secrets"}', False, True),
        ('```json\n{"verdict": "allow", "reason": "the user asked"}\n```', False, False),
        (None, False, False),
        (None, True, True),
        ('{"verdict": "maybe", "reason": ""}', True, True),
    ],
)
def test_judge_verdicts(answer, fail_closed, blocked):
    guard = review(Config(scanner=SCANNER, judge=judge_returning(answer), judge_fail_closed=fail_closed))
    assert bool(guard.flagged) == blocked


def test_judge_is_only_asked_about_suspicious_calls():
    seen: list = []
    config = Config(scanner=SCANNER, judge=judge_returning('{"verdict": "block", "reason": "x"}', seen))
    assert not review(config, args='{"command": "ls"}').flagged
    assert seen == []


def test_judge_never_sees_reasoning_or_agent_text():
    seen: list = []
    request = json.loads(json.dumps(ANTHROPIC))
    request["tools"] = [{"name": "bash", "input_schema": {}}]
    review(Config(scanner=SCANNER, judge=judge_returning('{"verdict": "allow", "reason": "ok"}', seen)), request)
    prompt = json.dumps(seen[0]["messages"])
    assert "fix the tests" in prompt and "/proc/1/environ" in prompt
    assert "SECRET_REASONING" not in prompt and "Running them." not in prompt and "1 failed" not in prompt
    assert seen[0]["response_format"]["type"] == "json_schema"
