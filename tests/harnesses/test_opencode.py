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
# OpenCode provider -> (model, small model), as in docs/opencode.md.
PROVIDERS = {
    "anthropic": ("claude-sonnet-4-5", "claude-haiku-4-5"),
    "safeagents-openai": ("gpt-4.1", "gpt-4.1-mini"),
}


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    with FakeModel() as model, running_proxy(model.url, tmp_path_factory.mktemp("proxy")) as proxy:
        yield model, proxy


@pytest.fixture
def opencode_home(stack, tmp_path, provider) -> Path:
    """A home directory whose ~/.config/opencode/opencode.json points `provider` at the proxy."""
    _, proxy = stack
    model, small_model = PROVIDERS[provider]
    base_url = proxy.url + "/v1"
    if provider == "anthropic":
        config = {"provider": {"anthropic": {"options": {"baseURL": base_url}}}}
    else:
        limit = {"context": 1047576, "output": 32768}
        config = {
            "provider": {
                provider: {
                    "npm": "@ai-sdk/openai-compatible",
                    "name": "OpenAI via safeagents",
                    "options": {"baseURL": base_url, "apiKey": "{env:OPENAI_API_KEY}"},
                    "models": {
                        model: {"name": "GPT-4.1", "limit": limit},
                        small_model: {"name": "GPT-4.1 mini", "limit": limit},
                    },
                }
            },
            "disabled_providers": ["openai"],
        }
    config["$schema"] = "https://opencode.ai/config.json"
    config["model"] = f"{provider}/{model}"
    config["small_model"] = f"{provider}/{small_model}"
    # Not in the docs: OpenCode runs bash without asking by default; this keeps it that way.
    config["permission"] = {"bash": "allow"}
    home = tmp_path / "home"
    config_dir = home / ".config" / "opencode"
    config_dir.mkdir(parents=True)
    (config_dir / "opencode.json").write_text(json.dumps(config, indent=2))
    return home


def run_opencode(home: Path, workdir: Path, scenario: str) -> str:
    """Runs one non-interactive OpenCode prompt and returns its JSON event stream."""
    env = {
        **os.environ,
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_DATA_HOME": str(home / ".local" / "share"),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "XDG_STATE_HOME": str(home / ".local" / "state"),
        "ANTHROPIC_API_KEY": "test-key",
        "OPENAI_API_KEY": "test-key",
        "OPENCODE_DISABLE_AUTOUPDATE": "1",
        # Use the model catalog bundled with OpenCode instead of fetching models.opencode.ai.
        "OPENCODE_DISABLE_MODELS_FETCH": "1",
        # OpenCode installs @opencode-ai/plugin into its config directory in the
        # background; offline, that install fails fast instead of calling the npm registry.
        "npm_config_offline": "true",
    }
    project = workdir / "project"
    project.mkdir()
    # The prompt goes on stdin: `opencode run` wraps message arguments that contain
    # spaces in quotes and escapes their double quotes, which garbles the EXTRA marker.
    completed = subprocess.run(
        [OPENCODE_BIN, "run", "--format", "json"],
        cwd=project,
        env=env,
        input=scenario_prompt(scenario, tool=SHELL_TOOL, extra=SHELL_EXTRA),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, f"opencode failed:\n{completed.stderr}"
    return completed.stdout


@pytest.mark.parametrize("provider", list(PROVIDERS))
@pytest.mark.parametrize(("scenario", "reason"), BLOCKED_SCENARIOS)
def test_flagged_call_is_blocked(stack, opencode_home, tmp_path, provider, scenario, reason):
    model, proxy = stack
    model.requests.clear()
    output = run_opencode(opencode_home, tmp_path, scenario)
    check_blocked(model, proxy, output, scenario, SHELL_TOOL, reason)


@pytest.mark.parametrize("provider", list(PROVIDERS))
def test_clean_call_runs(stack, opencode_home, tmp_path, provider):
    model, _ = stack
    model.requests.clear()
    output = run_opencode(opencode_home, tmp_path, "clean")
    check_ran(model, output)
