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
"""Runs the real OpenCode CLI through safeagents, set up as in docs/opencode.md.

Skipped unless `opencode` is installed (or `OPENCODE_BIN` points at it).
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

OPENCODE_BIN = os.environ.get("OPENCODE_BIN") or shutil.which("opencode")
pytestmark = pytest.mark.skipif(OPENCODE_BIN is None, reason="opencode is not installed")

SHELL_TOOL = "bash"
# OpenCode's bash tool also requires a description (no spaces: the marker ends at one).
SHELL_EXTRA = {"description": "scripted-step"}
# The two setups from docs/opencode.md: the built-in `anthropic` provider (Anthropic
# Messages) and an `@ai-sdk/openai-compatible` provider (OpenAI Chat Completions).
PROVIDERS = ["anthropic", "safeagents-openai"]
MODEL_LIMIT = {"context": 200000, "output": 8192}


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    with FakeModel() as model, running_proxy(model.url, tmp_path_factory.mktemp("proxy")) as proxy:
        yield model, proxy


@pytest.fixture
def opencode_config(stack, tmp_path) -> Path:
    """An opencode.json that points both providers at the proxy, with /v1 in `baseURL`."""
    _, proxy = stack
    models = {"fake-model": {"name": "Fake model", "limit": MODEL_LIMIT}}
    config = {
        "$schema": "https://opencode.ai/config.json",
        "provider": {
            "anthropic": {"options": {"baseURL": proxy.url + "/v1"}, "models": models},
            "safeagents-openai": {
                "npm": "@ai-sdk/openai-compatible",
                "name": "OpenAI via safeagents",
                "options": {"baseURL": proxy.url + "/v1", "apiKey": "{env:OPENAI_API_KEY}"},
                "models": models,
            },
        },
        "disabled_providers": ["openai"],
        "permission": {"bash": "allow"},
        "autoupdate": False,
        "share": "disabled",
    }
    config_path = tmp_path / "opencode.json"
    config_path.write_text(json.dumps(config, indent=2))
    return config_path


def run_opencode(config_path: Path, workdir: Path, provider: str, scenario: str) -> str:
    """Runs one non-interactive OpenCode prompt and returns its JSON event stream."""
    home = workdir / "home"
    home.mkdir()
    model = f"{provider}/fake-model"
    env = {
        **os.environ,
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_DATA_HOME": str(home / ".local" / "share"),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "XDG_STATE_HOME": str(home / ".local" / "state"),
        "OPENCODE_CONFIG": str(config_path),
        "OPENCODE_CONFIG_CONTENT": json.dumps({"model": model, "small_model": model}),
        "OPENCODE_DISABLE_AUTOUPDATE": "1",
        "OPENCODE_DISABLE_MODELS_FETCH": "1",
        "OPENCODE_DISABLE_LSP_DOWNLOAD": "1",
        "OPENCODE_DISABLE_DEFAULT_PLUGINS": "1",
        "OPENCODE_DISABLE_CLAUDE_CODE": "1",
        "OPENCODE_DISABLE_EXTERNAL_SKILLS": "1",
        "ANTHROPIC_API_KEY": "test-key",
        "OPENAI_API_KEY": "test-key",
    }
    project = workdir / "project"
    project.mkdir()
    command = [
        OPENCODE_BIN,
        "run",
        "--format",
        "json",
        "--pure",
        "--model",
        model,
        scenario_prompt(scenario, tool=SHELL_TOOL, extra=SHELL_EXTRA),
    ]
    completed = subprocess.run(
        command,
        cwd=project,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, f"opencode failed:\n{completed.stderr}"
    return completed.stdout


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize(("scenario", "reason"), BLOCKED_SCENARIOS)
def test_flagged_call_is_blocked(stack, opencode_config, tmp_path, provider, scenario, reason):
    model, proxy = stack
    model.requests.clear()
    output = run_opencode(opencode_config, tmp_path, provider, scenario)
    check_blocked(model, proxy, output, scenario, SHELL_TOOL, reason)


@pytest.mark.parametrize("provider", PROVIDERS)
def test_clean_call_runs(stack, opencode_config, tmp_path, provider):
    model, _ = stack
    model.requests.clear()
    output = run_opencode(opencode_config, tmp_path, provider, "clean")
    check_ran(model, output)
