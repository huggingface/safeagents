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
"""Runs the real pi CLI through safeagents, set up as in docs/pi.md.

Skipped unless `pi` is installed (or `PI_BIN` points at it).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from fake_model import FakeModel, scenario_prompt
from stack import BLOCKED_SCENARIOS, check_blocked, check_ran, running_proxy

PI_BIN = os.environ.get("PI_BIN") or shutil.which("pi")
pytestmark = pytest.mark.skipif(PI_BIN is None, reason="pi is not installed")

SHELL_TOOL = "bash"
# pi provider -> (`api` value, whether the base URL takes /v1), as in docs/pi.md.
PROVIDERS = {
    "safeagents-anthropic": ("anthropic-messages", False),
    "safeagents-openai": ("openai-completions", True),
}


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    with FakeModel() as model, running_proxy(model.url, tmp_path_factory.mktemp("proxy")) as proxy:
        yield model, proxy


@pytest.fixture
def pi_home(stack, tmp_path) -> Path:
    """A pi agent directory whose models.json points both providers at the proxy."""
    _, proxy = stack
    providers = {}
    for provider, (api, with_v1) in PROVIDERS.items():
        providers[provider] = {
            "baseUrl": proxy.url + "/v1" if with_v1 else proxy.url,
            "api": api,
            "apiKey": "test-key",
            "models": [{"id": "fake-model"}],
        }
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    (agent_dir / "models.json").write_text(json.dumps({"providers": providers}, indent=2))
    return agent_dir


def run_pi(pi_home: Path, workdir: Path, provider: str, scenario: str) -> str:
    """Runs one non-interactive pi prompt and returns its JSON event stream."""
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
        SHELL_TOOL,
        scenario_prompt(scenario, tool=SHELL_TOOL),
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
    return completed.stdout


@pytest.mark.parametrize("provider", list(PROVIDERS))
@pytest.mark.parametrize(("scenario", "reason"), BLOCKED_SCENARIOS)
def test_flagged_call_is_blocked(stack, pi_home, tmp_path, provider, scenario, reason):
    model, proxy = stack
    model.requests.clear()
    output = run_pi(pi_home, tmp_path, provider, scenario)
    check_blocked(model, proxy, output, scenario, SHELL_TOOL, reason)


@pytest.mark.parametrize("provider", list(PROVIDERS))
def test_clean_call_runs(stack, pi_home, tmp_path, provider):
    model, _ = stack
    model.requests.clear()
    output = run_pi(pi_home, tmp_path, provider, "clean")
    check_ran(model, output)
