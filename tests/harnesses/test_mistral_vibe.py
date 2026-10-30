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
"""Runs the real Mistral Vibe CLI through safeagents, set up as in docs/mistral-vibe.md.

Skipped unless `vibe` is installed (or `VIBE_BIN` points at it).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from fake_model import FakeModel, scenario_prompt
from stack import BLOCKED_SCENARIOS, check_blocked, check_ran, running_proxy

VIBE_BIN = os.environ.get("VIBE_BIN") or shutil.which("vibe")
pytestmark = pytest.mark.skipif(VIBE_BIN is None, reason="vibe is not installed")

SHELL_TOOL = "bash"
SHELL_ARG = "command"
# Vibe provider -> its `[[providers]]` lines, minus the base URL, and whether the
# base URL takes /v1, as in docs/mistral-vibe.md.
PROVIDERS = {
    "mistral": ('backend = "mistral"\napi_key_env_var = "MISTRAL_API_KEY"', True),
    "openai": ('api_style = "openai"\napi_key_env_var = "OPENAI_API_KEY"', True),
    "anthropic": ('api_style = "anthropic"\napi_key_env_var = "ANTHROPIC_API_KEY"', False),
}
# Environment variables that would override the config file or leak real credentials.
ISOLATED_PREFIXES = ("MISTRAL_", "OPENAI_", "ANTHROPIC_", "VIBE_")


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    with FakeModel() as model, running_proxy(model.url, tmp_path_factory.mktemp("proxy")) as proxy:
        yield model, proxy


@pytest.fixture
def vibe_home(stack, tmp_path, provider) -> Path:
    """A ~/.vibe whose config.toml points `provider` at the proxy."""
    _, proxy = stack
    lines, with_v1 = PROVIDERS[provider]
    base_url = proxy.url + "/v1" if with_v1 else proxy.url
    config = f"""\
active_model = "fake"

[utility_models]
title = "active"
smart_approve = "active"

[[providers]]
name = "{provider}"
api_base = "{base_url}"
{lines}

[[models]]
name = "fake-model"
provider = "{provider}"
alias = "fake"
"""
    # Not in the docs: keep the run off Mistral's telemetry and update servers.
    config = "enable_telemetry = false\nenable_update_checks = false\nenable_auto_update = false\n" + config
    home = tmp_path / "home" / ".vibe"
    home.mkdir(parents=True)
    (home / "config.toml").write_text(config)
    return home


def run_vibe(vibe_home: Path, workdir: Path, scenario: str) -> str:
    """Runs one headless Vibe prompt and returns its text output."""
    env = {}
    for name, value in os.environ.items():
        if not name.startswith(ISOLATED_PREFIXES):
            env[name] = value
    env.update(
        {
            "HOME": str(vibe_home.parent),
            "VIBE_HOME": str(vibe_home),
            "MISTRAL_API_KEY": "test-key",
            "OPENAI_API_KEY": "test-key",
            "ANTHROPIC_API_KEY": "test-key",
        }
    )
    command = [
        VIBE_BIN,
        "--prompt",
        scenario_prompt(scenario, tool=SHELL_TOOL, arg=SHELL_ARG),
        "--auto-approve",
        "--output",
        "text",
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
    assert completed.returncode == 0, f"vibe failed:\n{completed.stderr}"
    return completed.stdout


@pytest.mark.parametrize("provider", list(PROVIDERS))
@pytest.mark.parametrize(("scenario", "reason"), BLOCKED_SCENARIOS)
def test_flagged_call_is_blocked(stack, vibe_home, tmp_path, provider, scenario, reason):
    model, proxy = stack
    model.requests.clear()
    output = run_vibe(vibe_home, tmp_path, scenario)
    check_blocked(model, proxy, output, scenario, SHELL_TOOL, reason)


@pytest.mark.parametrize("provider", list(PROVIDERS))
def test_clean_call_runs(stack, vibe_home, tmp_path, provider):
    model, _ = stack
    model.requests.clear()
    output = run_vibe(vibe_home, tmp_path, "clean")
    check_ran(model, output)
