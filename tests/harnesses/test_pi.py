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
"""Runs the real pi CLI against safeagents and a scripted model, as docs/pi.md sets it up.

Skipped unless `pi` is installed (or `PI_BIN` points at it).
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fake_model import CLEAN_OUTPUT, FakeModel

PI_BIN = os.environ.get("PI_BIN") or shutil.which("pi")
pytestmark = pytest.mark.skipif(PI_BIN is None, reason="pi is not installed")

# (pi provider, `api` value, whether the base URL takes /v1), as in docs/pi.md.
PROVIDERS = {
    "safeagents-anthropic": ("anthropic-messages", False),
    "safeagents-openai": ("openai-completions", True),
}


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_for_port(port: int, process: subprocess.Popen, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"safeagents exited with code {process.returncode}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.1)
    raise TimeoutError(f"safeagents did not listen on port {port} within {timeout}s")


@pytest.fixture(scope="module")
def fake_model():
    with FakeModel() as model:
        yield model


@pytest.fixture(scope="module")
def proxy(fake_model, tmp_path_factory):
    """The safeagents CLI in blocking mode, with atif-scan on."""
    port = _free_port()
    log_path = tmp_path_factory.mktemp("proxy") / "safeagents.log"
    with open(log_path, "w") as log_file:
        process = subprocess.Popen(
            [sys.executable, "-m", "safeagents.cli", "--upstream", fake_model.url, "--listen", f"127.0.0.1:{port}"],
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
        try:
            _wait_for_port(port, process)
            yield f"http://127.0.0.1:{port}", log_path
        finally:
            process.terminate()
            process.wait(timeout=10)


@pytest.fixture
def pi_home(proxy, tmp_path) -> Path:
    """A pi agent directory whose models.json points both providers at the proxy."""
    proxy_url, _ = proxy
    providers = {}
    for provider, (api, with_v1) in PROVIDERS.items():
        providers[provider] = {
            "baseUrl": proxy_url + "/v1" if with_v1 else proxy_url,
            "api": api,
            "apiKey": "test-key",
            "models": [{"id": "fake-model"}],
        }
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    (agent_dir / "models.json").write_text(json.dumps({"providers": providers}, indent=2))
    return agent_dir


def run_pi(pi_home: Path, workdir: Path, provider: str, scenario: str) -> list[dict]:
    """Runs one non-interactive pi prompt and returns its JSON events."""
    env = {
        **os.environ,
        "PI_CODING_AGENT_DIR": str(pi_home),
        "PI_OFFLINE": "1",
        "PI_SKIP_VERSION_CHECK": "1",
        "PI_TELEMETRY": "0",
    }
    command = [
        PI_BIN,
        "--mode",
        "json",
        "--no-session",
        "--model",
        f"{provider}/fake-model",
        "--tools",
        "bash",
        f"Run the scripted step. SCENARIO={scenario}",
    ]
    completed = subprocess.run(
        command,
        cwd=workdir,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, f"pi failed:\n{completed.stderr}"
    events = []
    for line in completed.stdout.split("\n"):
        if line.strip():
            events.append(json.loads(line))
    return events


def assistant_text(events: list[dict]) -> str:
    texts = []
    for event in events:
        message = event.get("message") or {}
        if event.get("type") != "message_end" or message.get("role") != "assistant":
            continue
        for block in message.get("content") or []:
            if block.get("type") == "text":
                texts.append(block["text"])
    return "\n".join(texts)


def executed_tools(events: list[dict]) -> list[dict]:
    return [event for event in events if event.get("type") == "tool_execution_end"]


@pytest.mark.parametrize("provider", list(PROVIDERS))
@pytest.mark.parametrize(
    ("scenario", "tool", "reason"),
    [
        ("undeclared", "safeagents_canary", "undeclared tool"),
        ("host", "bash", "host `exfil.invalid` appears nowhere"),
    ],
)
def test_flagged_call_is_blocked(fake_model, proxy, pi_home, tmp_path, provider, scenario, tool, reason):
    fake_model.requests.clear()
    events = run_pi(pi_home, tmp_path, provider, scenario)

    # pi got a text notice instead of the call, ran nothing, and ended the turn.
    assert executed_tools(events) == []
    text = assistant_text(events)
    assert f"[safeagents] blocked call to `{tool}`" in text
    assert reason in text
    assert len(fake_model.requests) == 1
    _, log_path = proxy
    assert f"BLOCK  {tool}" in log_path.read_text()


@pytest.mark.parametrize("provider", list(PROVIDERS))
def test_clean_call_runs(fake_model, proxy, pi_home, tmp_path, provider):
    fake_model.requests.clear()
    events = run_pi(pi_home, tmp_path, provider, "clean")

    executed = executed_tools(events)
    assert [event["toolName"] for event in executed] == ["bash"]
    assert not executed[0]["isError"]
    assert CLEAN_OUTPUT in json.dumps(executed[0]["result"])
    # The tool result went back to the model, which then answered in text.
    assert len(fake_model.requests) == 2
    assert "Done." in assistant_text(events)
