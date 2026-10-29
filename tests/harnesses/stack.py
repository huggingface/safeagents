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
"""The safeagents CLI in front of the fake model, plus the checks every harness test makes.

A harness test runs the harness once per scenario, as its docs/<harness>.md page sets
it up, and passes everything the harness printed to `check_blocked` or `check_ran`.
The checks look at what the fake model received, so they don't depend on how a
harness reports tool calls: a tool that ran sends its result back to the model.
"""

from __future__ import annotations

import contextlib
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from fake_model import CANARY_TOOL, CLEAN_OUTPUT, EXFIL_HOST, FakeModel

# (scenario, expected reason), for scenarios safeagents must block.
BLOCKED_SCENARIOS = [
    ("undeclared", "undeclared tool"),
    ("host", f"host `{EXFIL_HOST}` appears nowhere in the conversation"),
]


@dataclass
class Proxy:
    url: str
    log_path: Path

    def log(self) -> str:
        return self.log_path.read_text()


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


@contextlib.contextmanager
def running_proxy(upstream: str, log_dir: Path) -> Iterator[Proxy]:
    """The safeagents CLI in blocking mode, with atif-scan on, logging to `log_dir`."""
    port = _free_port()
    log_path = log_dir / "safeagents.log"
    with open(log_path, "w") as log_file:
        process = subprocess.Popen(
            [sys.executable, "-m", "safeagents.cli", upstream, str(port)],
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
        try:
            _wait_for_port(port, process)
            yield Proxy(f"http://127.0.0.1:{port}", log_path)
        finally:
            process.terminate()
            process.wait(timeout=10)


def check_blocked(model: FakeModel, proxy: Proxy, output: str, scenario: str, shell_tool: str, reason: str) -> None:
    """The harness ran nothing and showed safeagents' notice instead of the call."""
    tool = CANARY_TOOL if scenario == "undeclared" else shell_tool
    assert model.scripted_requests(), "the scripted request never reached the model"
    assert model.tool_results() == [], f"a tool ran: {model.tool_results()}"
    assert f"[safeagents] blocked call to `{tool}`" in output
    assert reason in output
    assert f"BLOCK  {tool}" in proxy.log()


def check_ran(model: FakeModel, output: str) -> None:
    """The clean call ran, its result went back to the model, and the turn ended in text."""
    results = model.tool_results()
    assert len(results) == 1, f"expected one tool result, got {results}"
    assert CLEAN_OUTPUT in results[0]
    assert "[safeagents]" not in output
