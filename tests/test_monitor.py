import asyncio
import io

import httpx
from conftest import OSS, bash_request
from rich.console import Console

from safeagents import Config, Guard
from safeagents.monitor import Monitor, summary
from safeagents.proxy import create_app
from safeagents.scan import Scanner


def monitor() -> tuple[Monitor, io.StringIO]:
    out = io.StringIO()
    return Monitor(Console(file=out, width=200, force_terminal=False, color_system=None)), out


def test_every_verdict_is_printed_and_counted():
    m, out = monitor()
    req = bash_request("Write a detection rule for curl downloads to /tmp.")

    async def go():
        blocking = Guard(req, Config(scanner=Scanner(), monitor=m))
        await blocking.allow("bash", '{"command": "ls"}')
        await blocking.allow("bash", '{"command": "cat /proc/1/environ"}')
        await blocking.allow("bash", f'{{"command": "curl https://{OSS}"}}')
        await Guard(req, Config(scanner=None, block=False, monitor=m)).allow("shell", "{}")

    asyncio.run(go())
    lines = out.getvalue().splitlines()
    assert [line.split()[1] for line in lines] == ["PASS", "WARN", "BLOCK", "FLAG"]
    assert "cat /proc/1/environ  side_channel.harness_secret_read (medium)" in lines[1]
    assert f"host `{OSS}` appears nowhere" in lines[2]
    assert {k: m.counts[k] for k in ("calls", "passed", "suspicious", "flagged", "blocked")} == {
        "calls": 4, "passed": 1, "suspicious": 1, "flagged": 1, "blocked": 1,
    }


def test_terminal_view_draws_histogram_and_latest_calls():
    console = Console(file=io.StringIO(), width=100, height=20, force_terminal=True, color_system=None)
    m = Monitor(console, setup="127.0.0.1:8787 -> upstream")
    for verdict in ("passed", "passed", "passed", "blocked"):
        m.record("Bash", '{"command": "ls"}', verdict, "nope" if verdict == "blocked" else None, [], None)
    console.print(m.render())
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
    m, _ = monitor()
    body = {"content": [{"type": "text", "text": "hi"}], "stop_reason": "end_turn"}
    app = create_app("http://model", Config(scanner=None, monitor=m),
                     transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body)))

    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://proxy") as client:
            await client.post("/v1/messages", json=bash_request("hi"))
            await client.post("/v1/messages/count_tokens", json=bash_request("hi"))
            await client.post("/api/event_logging/batch", json={"events": []})

    asyncio.run(go())
    assert m.counts["requests"] == 1
