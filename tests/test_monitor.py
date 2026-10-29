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
import io

import httpx
from conftest import OSS, bash_request
from rich.console import Console

from safeagents import Config, Guard
from safeagents.monitor import Monitor, summary
from safeagents.proxy import create_app
from safeagents.scan import Scanner


def make_monitor() -> tuple[Monitor, io.StringIO]:
    out = io.StringIO()
    return Monitor(Console(file=out, width=200, force_terminal=False, color_system=None)), out


def test_every_verdict_is_printed_and_counted():
    monitor, out = make_monitor()
    request = bash_request("Write a detection rule for curl downloads to /tmp.")

    async def go():
        blocking = Guard(request, Config(scanner=Scanner(), monitor=monitor))
        await blocking.allow("bash", '{"command": "ls"}')
        await blocking.allow("bash", '{"command": "cat /proc/1/environ"}')
        await blocking.allow("bash", f'{{"command": "curl https://{OSS}"}}')
        await Guard(request, Config(scanner=None, block=False, monitor=monitor)).allow("shell", "{}")

    asyncio.run(go())
    lines = out.getvalue().splitlines()
    assert [line.split()[1] for line in lines] == ["PASS", "WARN", "BLOCK", "FLAG"]
    assert "cat /proc/1/environ  side_channel.harness_secret_read (medium)" in lines[1]
    assert f"host `{OSS}` appears nowhere" in lines[2]
    assert {key: monitor.counts[key] for key in ("calls", "passed", "suspicious", "flagged", "blocked")} == {
        "calls": 4, "passed": 1, "suspicious": 1, "flagged": 1, "blocked": 1,
    }


def test_terminal_view_draws_histogram_and_latest_calls():
    console = Console(file=io.StringIO(), width=100, height=20, force_terminal=True, color_system=None)
    monitor = Monitor(console, setup="127.0.0.1:8787 -> upstream")
    for verdict in ("passed", "passed", "passed", "blocked"):
        monitor.record("Bash", '{"command": "ls"}', verdict, "nope" if verdict == "blocked" else None, [], None)
    console.print(monitor.render())
    screen = console.file.getvalue()
    assert "╔═  SAFEAGENTS " in screen and "╔═  CALLS " in screen
    bars = {line.split()[1]: line.count("█") for line in screen.splitlines() if "%" in line}
    assert bars["BLOCK"] > 0 and abs(bars["PASS"] - 3 * bars["BLOCK"]) <= 2 and bars["WARN"] == bars["FLAG"] == 0
    assert screen.count("nope") == 1


def test_summary_picks_the_telling_argument():
    assert summary('{"description": "x", "command": "ls  -la\\n src"}') == "ls -la src"
    assert summary('{"url": "https://a.b/c", "prompt": "p"}') == "https://a.b/c"
    assert summary('{"a": 1}') == '{"a": 1}'
    assert len(summary('{"command": "' + "x" * 500 + '"}')) == 60


def test_only_model_turns_count_as_requests():
    monitor, _ = make_monitor()
    body = {"content": [{"type": "text", "text": "hi"}], "stop_reason": "end_turn"}
    app = create_app("http://model", Config(scanner=None, monitor=monitor),
                     transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body)))

    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://proxy") as client:
            await client.post("/v1/messages", json=bash_request("hi"))
            await client.post("/v1/messages/count_tokens", json=bash_request("hi"))
            await client.post("/api/event_logging/batch", json={"events": []})

    asyncio.run(go())
    assert monitor.counts["requests"] == 1
