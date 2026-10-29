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
"""Runs the OpenHands Software Agent SDK through safeagents, set up as in docs/openhands.md.

The SDK lives in its own environment: `OPENHANDS_PYTHON` points at a Python that has
openhands-sdk and openhands-tools installed, and each test runs openhands_driver.py
with it. Skipped unless `OPENHANDS_PYTHON` is set and can import the SDK.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from fake_model import FakeModel, scenario_prompt
from stack import BLOCKED_SCENARIOS, check_blocked, check_ran, running_proxy

DRIVER = Path(__file__).with_name("openhands_driver.py")
SHELL_TOOL = "terminal"
SHELL_ARG = "command"
# Driver routes: `anthropic/` models on Anthropic Messages, `openai/` models on Chat Completions.
ROUTES = ["anthropic", "openai"]


def find_openhands_python() -> str | None:
    """`OPENHANDS_PYTHON`, if it is set and can import the SDK and its terminal tool."""
    python = os.environ.get("OPENHANDS_PYTHON")
    if not python:
        return None
    try:
        completed = subprocess.run(
            [python, "-c", "import openhands.sdk, openhands.tools.terminal"],
            env={**os.environ, "LITELLM_LOCAL_MODEL_COST_MAP": "True", "OPENHANDS_SUPPRESS_BANNER": "1"},
            capture_output=True,
            timeout=120,
            check=False,
        )
    except OSError:
        return None
    if completed.returncode != 0:
        return None
    return python


OPENHANDS_PYTHON = find_openhands_python()
pytestmark = pytest.mark.skipif(OPENHANDS_PYTHON is None, reason="OPENHANDS_PYTHON is unset or lacks openhands-sdk")


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    with FakeModel() as model, running_proxy(model.url, tmp_path_factory.mktemp("proxy")) as proxy:
        yield model, proxy


def run_openhands(proxy_url: str, workdir: Path, route: str, scenario: str) -> str:
    """Runs one SDK conversation through the proxy and returns its events, one JSON line each."""
    env = {
        **os.environ,
        "HOME": str(workdir),
        "LITELLM_LOCAL_MODEL_COST_MAP": "True",
        "OPENHANDS_SUPPRESS_BANNER": "1",
    }
    command = [
        OPENHANDS_PYTHON,
        str(DRIVER),
        "--route",
        route,
        "--base-url",
        proxy_url,
        "--workdir",
        str(workdir),
        "--prompt",
        scenario_prompt(scenario, tool=SHELL_TOOL, arg=SHELL_ARG),
    ]
    completed = subprocess.run(
        command,
        cwd=workdir,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert completed.returncode == 0, f"the OpenHands driver failed:\n{completed.stderr}"
    return completed.stdout


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize(("scenario", "reason"), BLOCKED_SCENARIOS)
def test_flagged_call_is_blocked(stack, tmp_path, route, scenario, reason):
    model, proxy = stack
    model.requests.clear()
    output = run_openhands(proxy.url, tmp_path, route, scenario)
    check_blocked(model, proxy, output, scenario, SHELL_TOOL, reason)


@pytest.mark.parametrize("route", ROUTES)
def test_clean_call_runs(stack, tmp_path, route):
    model, proxy = stack
    model.requests.clear()
    output = run_openhands(proxy.url, tmp_path, route, "clean")
    check_ran(model, output)
