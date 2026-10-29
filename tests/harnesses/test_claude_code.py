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
"""Runs the real Claude Code CLI through safeagents, set up as in docs/claude-code.md.

Skipped unless `claude` is installed (or `CLAUDE_BIN` points at it).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from fake_model import FakeModel, scenario_prompt
from stack import BLOCKED_SCENARIOS, check_blocked, check_ran, running_proxy

CLAUDE_BIN = os.environ.get("CLAUDE_BIN") or shutil.which("claude")
pytestmark = pytest.mark.skipif(CLAUDE_BIN is None, reason="Claude Code is not installed")

SHELL_TOOL = "Bash"
# Variables from the calling shell that would change which account, endpoint or
# settings Claude Code uses.
INHERITED_PREFIXES = ("ANTHROPIC_", "CLAUDE")


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    with FakeModel() as model, running_proxy(model.url, tmp_path_factory.mktemp("proxy")) as proxy:
        yield model, proxy


@pytest.fixture
def claude_home(tmp_path) -> Path:
    """An empty home directory, so no user settings, logins or CLAUDE.md files apply."""
    home = tmp_path / "home"
    home.mkdir()
    return home


def run_claude(claude_home: Path, workdir: Path, base_url: str, scenario: str) -> str:
    """Runs one headless Claude Code prompt and returns its JSON event stream."""
    env = {}
    for name, value in os.environ.items():
        if not name.startswith(INHERITED_PREFIXES):
            env[name] = value
    env.update(
        {
            "HOME": str(claude_home),
            "CLAUDE_CONFIG_DIR": str(claude_home / ".claude"),
            # As in docs/claude-code.md: the proxy URL, without /v1.
            "ANTHROPIC_BASE_URL": base_url,
            "ANTHROPIC_API_KEY": "test-key",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "DISABLE_AUTOUPDATER": "1",
        }
    )
    # The prompt comes first: `--tools` and `--allowedTools` take every value after them.
    command = [
        CLAUDE_BIN,
        "--print",
        scenario_prompt(scenario, tool=SHELL_TOOL),
        "--output-format",
        "stream-json",
        "--verbose",
        "--no-session-persistence",
        "--tools",
        SHELL_TOOL,
        "--allowedTools",
        SHELL_TOOL,
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
    assert completed.returncode == 0, f"Claude Code failed:\n{completed.stderr}"
    return completed.stdout


@pytest.mark.parametrize(("scenario", "reason"), BLOCKED_SCENARIOS)
def test_flagged_call_is_blocked(stack, claude_home, tmp_path, scenario, reason):
    model, proxy = stack
    model.requests.clear()
    output = run_claude(claude_home, tmp_path, proxy.url, scenario)
    check_blocked(model, proxy, output, scenario, SHELL_TOOL, reason)


def test_clean_call_runs(stack, claude_home, tmp_path):
    model, proxy = stack
    model.requests.clear()
    output = run_claude(claude_home, tmp_path, proxy.url, "clean")
    check_ran(model, output)
