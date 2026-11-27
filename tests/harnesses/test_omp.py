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
"""Runs the real OMP (oh-my-pi) CLI through safeagents, set up as in docs/omp.md.

Skipped unless `omp` is installed (or `OMP_BIN` points at it).
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

OMP_BIN = os.environ.get("OMP_BIN") or shutil.which("omp")
pytestmark = pytest.mark.skipif(OMP_BIN is None, reason="omp is not installed")

SHELL_TOOL = "bash"
# OMP provider -> (`api` value, whether the base URL takes /v1), as in docs/omp.md.
PROVIDERS = {
    "safeagents-anthropic": ("anthropic-messages", False),
    "safeagents-openai": ("openai-completions", True),
}


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    with FakeModel() as model, running_proxy(model.url, tmp_path_factory.mktemp("proxy")) as proxy:
        yield model, proxy


@pytest.fixture
def omp_home(stack, tmp_path) -> Path:
    """A home directory whose `~/.omp/agent/models.yml` points both providers at the proxy."""
    _, proxy = stack
    providers = {}
    for provider, (api, with_v1) in PROVIDERS.items():
        providers[provider] = {
            "baseUrl": proxy.url + "/v1" if with_v1 else proxy.url,
            "api": api,
            "apiKey": "test-key",
            "models": [{"id": "fake-model"}],
        }
    home = tmp_path / "home"
    agent_dir = home / ".omp" / "agent"
    agent_dir.mkdir(parents=True)
    # JSON is valid YAML.
    (agent_dir / "models.yml").write_text(json.dumps({"providers": providers}, indent=2))
    return home


def run_omp(home: Path, workdir: Path, provider: str, scenario: str) -> str:
    """Runs one non-interactive OMP prompt and returns its JSON event stream."""
    # Without provider keys, OMP doesn't list the models of its built-in providers at startup.
    env = {key: value for key, value in os.environ.items() if not key.endswith(("_API_KEY", "_TOKEN"))}
    for key in ("OMP_PROFILE", "PI_PROFILE", "PI_CODING_AGENT_DIR", "PI_CONFIG_DIR"):
        env.pop(key, None)
    env["HOME"] = str(home)
    command = [
        OMP_BIN,
        "--mode",
        "json",
        "--no-session",
        "--no-title",
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
    assert completed.returncode == 0, f"omp failed:\n{completed.stderr}"
    return completed.stdout


@pytest.fixture
def workdir(tmp_path) -> Path:
    path = tmp_path / "work"
    path.mkdir()
    return path


@pytest.mark.parametrize("provider", list(PROVIDERS))
@pytest.mark.parametrize(("scenario", "reason"), BLOCKED_SCENARIOS)
def test_flagged_call_is_blocked(stack, omp_home, workdir, provider, scenario, reason):
    model, proxy = stack
    model.requests.clear()
    output = run_omp(omp_home, workdir, provider, scenario)
    check_blocked(model, proxy, output, scenario, SHELL_TOOL, reason)


@pytest.mark.parametrize("provider", list(PROVIDERS))
def test_clean_call_runs(stack, omp_home, workdir, provider):
    model, _ = stack
    model.requests.clear()
    output = run_omp(omp_home, workdir, provider, "clean")
    check_ran(model, output)
