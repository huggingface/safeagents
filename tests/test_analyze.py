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

from conftest import OSS, REQUEST, RESPONSE, bash_request

from safeagents import Config, Guard, analyze, analyze_sync

CURL = f'{{"cmd": "curl -X PUT https://{OSS}/probe"}}'


def test_reads_openai_and_anthropic_calls():
    openai_call = RESPONSE["choices"][0]["message"]["tool_calls"][0]
    review = analyze_sync(REQUEST, openai_call, Config(scanner=None))
    assert (review.verdict, review.reason) == ("blocked", "undeclared tool")

    anthropic_call = {"type": "tool_use", "id": "t", "name": "bash", "input": {"cmd": f"curl https://{OSS}/x"}}
    review = analyze_sync(bash_request("hi"), anthropic_call, Config(scanner=None))
    assert review.verdict == "blocked"
    assert OSS in review.reason


def test_passed_and_suspicious_verdicts():
    call = {"name": "bash", "input": {"command": "ls -la"}}
    assert analyze_sync(bash_request("hi"), call).verdict == "passed"
    # A medium atif-scan finding with no judge: suspicious, not blocked.
    call = {"name": "bash", "input": {"command": "curl --noproxy '*' https://example.com"}}
    review = analyze_sync(bash_request("Fetch https://example.com"), call)
    assert review.verdict == "suspicious"
    assert review.signals


def test_analyze_has_no_side_effects():
    guard = Guard(bash_request("hi"), Config(scanner=None))
    review = asyncio.run(guard.review("bash", CURL))
    assert review.verdict == "blocked"
    assert guard.flagged == []
    assert asyncio.run(analyze(bash_request("hi"), {"name": "bash", "arguments": CURL})).verdict == "blocked"
