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
"""Runs the real DeepSeek Harness (dsh) CLI through safeagents, set up as in docs/deepseek-harness.md.

Uses the native `llm-deepseek` adapter (Anthropic Messages) with an API key and
`DEEPSEEK_BASE_URL` pointing at the proxy. Skipped unless `dsh` is installed (or
`DSH_BIN` points at it).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from fake_model import FakeModel, scenario_prompt
from stack import BLOCKED_SCENARIOS, check_blocked, check_ran, running_proxy

DSH_BIN = os.environ.get("DSH_BIN") or shutil.which("dsh")
pytestmark = pytest.mark.skipif(DSH_BIN is None, reason="dsh is not installed")

SHELL_TOOL = "bash"
# dsh's bash tool requires a description next to the command. Markers can't hold spaces.
BASH_EXTRA = {"description": "scripted-step"}
# docs/deepseek-harness.md: dsh adds /v1 when it's missing, so both forms work.
BASE_URL_SUFFIXES = {"no-v1": "", "v1": "/v1"}


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    with FakeModel() as model, running_proxy(model.url, tmp_path_factory.mktemp("proxy")) as proxy:
        yield model, proxy


def run_dsh(home: Path, workdir: Path, base_url: str, scenario: str) -> str:
    """Runs one headless dsh task and returns its JSON event stream."""
    env = {
        **os.environ,
        "HOME": str(home),
        "DSH_HOME": str(home / ".dsh"),
        "DEEPSEEK_API_KEY": "test-key",
        "DEEPSEEK_BASE_URL": base_url,
        "DSH_TELEMETRY_DISABLED": "1",
    }
    command = [
        DSH_BIN,
        "headless",
        "--json",
        scenario_prompt(scenario, tool=SHELL_TOOL, extra=BASH_EXTRA),
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
    assert completed.returncode == 0, f"dsh failed:\n{completed.stderr}"
    return completed.stdout


@pytest.fixture
def dirs(tmp_path) -> tuple[Path, Path]:
    """An isolated home (and DSH_HOME inside it) and an empty workspace."""
    home = tmp_path / "home"
    workdir = tmp_path / "work"
    home.mkdir()
    workdir.mkdir()
    return home, workdir


@pytest.mark.parametrize("suffix", list(BASE_URL_SUFFIXES))
@pytest.mark.parametrize(("scenario", "reason"), BLOCKED_SCENARIOS)
def test_flagged_call_is_blocked(stack, dirs, suffix, scenario, reason):
    model, proxy = stack
    model.requests.clear()
    output = run_dsh(*dirs, proxy.url + BASE_URL_SUFFIXES[suffix], scenario)
    check_blocked(model, proxy, output, scenario, SHELL_TOOL, reason)


@pytest.mark.parametrize("suffix", list(BASE_URL_SUFFIXES))
def test_clean_call_runs(stack, dirs, suffix):
    model, proxy = stack
    model.requests.clear()
    output = run_dsh(*dirs, proxy.url + BASE_URL_SUFFIXES[suffix], "clean")
    check_ran(model, output)
