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

Skipped unless `opencode` is installed (or `OPENCODE_BIN` points at it). Works with
OpenCode 1.x and 2.x: the major version picks the config format, the shell tool and
the models, as the two halves of docs/opencode.md do.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fake_model import FakeModel, scenario_prompt
from stack import BLOCKED_SCENARIOS, check_blocked, check_ran, running_proxy

OPENCODE_BIN = os.environ.get("OPENCODE_BIN") or shutil.which("opencode")
pytestmark = pytest.mark.skipif(OPENCODE_BIN is None, reason="opencode is not installed")


def _major_version(binary: str | None) -> int:
    """1.x prints `1.19.3`, 2.x prints `opencode v2.3.0`."""
    if binary is None:
        return 1
    output = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=60, check=False).stdout
    match = re.search(r"(\d+)\.\d+\.\d+", output)
    return int(match.group(1)) if match else 1


V2 = _major_version(OPENCODE_BIN) >= 2
# 1.x calls its shell tool `bash` and requires a description (no spaces: the marker
# ends at one); 2.x calls it `shell` and rejects any argument besides its own.
SHELL_TOOL = "shell" if V2 else "bash"
SHELL_EXTRA = None if V2 else {"description": "scripted-step"}
# OpenCode provider -> (model, small model), as in docs/opencode.md. 2.x's catalog no
# longer lists claude-sonnet-4-5, and 2.x silently swaps an unknown model for its default.
PROVIDERS = {
    "anthropic": ("claude-sonnet-5-5", "claude-haiku-5-5") if V2 else ("claude-sonnet-4-5", "claude-haiku-4-5"),
    "safeagents-openai": ("gpt-4.1", "gpt-4.1-mini"),
}
OPENAI_LIMIT = {"context": 1047576, "output": 32768}


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    with FakeModel() as model, running_proxy(model.url, tmp_path_factory.mktemp("proxy")) as proxy:
        yield model, proxy


def config_v1(provider: str, base_url: str) -> dict:
    model, small_model = PROVIDERS[provider]
    if provider == "anthropic":
        config = {"provider": {"anthropic": {"options": {"baseURL": base_url}}}}
    else:
        config = {
            "provider": {
                provider: {
                    "npm": "@ai-sdk/openai-compatible",
                    "name": "OpenAI via safeagents",
                    "options": {"baseURL": base_url, "apiKey": "{env:OPENAI_API_KEY}"},
                    "models": {
                        model: {"name": "GPT-4.1", "limit": OPENAI_LIMIT},
                        small_model: {"name": "GPT-4.1 mini", "limit": OPENAI_LIMIT},
                    },
                }
            },
            "disabled_providers": ["openai"],
        }
    config["model"] = f"{provider}/{model}"
    config["small_model"] = f"{provider}/{small_model}"
    # Not in the docs: OpenCode runs bash without asking by default; this keeps it that way.
    config["permission"] = {"bash": "allow"}
    return config


def config_v2(provider: str, base_url: str) -> dict:
    model, small_model = PROVIDERS[provider]
    if provider == "anthropic":
        config = {"providers": {"anthropic": {"settings": {"baseURL": base_url}}}}
    else:
        config = {
            "providers": {
                provider: {
                    "name": "OpenAI via safeagents",
                    "package": "@opencode/ai/providers/openai-compatible",
                    "env": ["OPENAI_API_KEY"],
                    "settings": {"baseURL": base_url, "apiKey": "{env:OPENAI_API_KEY}"},
                    "models": {
                        model: {"name": "GPT-4.1", "limit": OPENAI_LIMIT},
                        small_model: {"name": "GPT-4.1 mini", "limit": OPENAI_LIMIT},
                    },
                }
            },
            "policies": [{"action": "provider.use", "resource": "openai", "effect": "deny"}],
        }
    config["model"] = f"{provider}/{model}"
    config["agents"] = {"title": {"model": f"{provider}/{small_model}"}}
    # Not in the docs: a run must not stop to ask for approval or offer an update.
    config["permissions"] = [{"action": "shell", "resource": "*", "effect": "allow"}]
    config["update"] = "disable"
    return config


@pytest.fixture
def opencode_home(stack, tmp_path, provider) -> Path:
    """A home directory whose ~/.config/opencode/opencode.json points `provider` at the proxy."""
    _, proxy = stack
    base_url = proxy.url + "/v1"
    config = {"$schema": "https://opencode.ai/config.json", **(config_v2 if V2 else config_v1)(provider, base_url)}
    home = tmp_path / "home"
    config_dir = home / ".config" / "opencode"
    config_dir.mkdir(parents=True)
    (config_dir / "opencode.json").write_text(json.dumps(config, indent=2))
    return home


# 2.x runs sessions in a background service shared by the user's OpenCode processes,
# which keeps the environment it started with; `--standalone` gives each run its own
# server (and is what makes `--print-logs` include the server's logs).
RUN_FLAGS = (
    ["--standalone", "--format", "json", "--print-logs", "--log-level", "info"]
    if V2
    else ["--format", "json", "--print-logs", "--log-level", "INFO"]
)


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
        # Logs go to stderr, which a failure prints: `opencode run` itself only reports
        # "Unexpected server error. Check server logs for details."
        [OPENCODE_BIN, "run", *RUN_FLAGS],
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


def check_model(model: FakeModel, provider: str) -> None:
    """OpenCode called the configured model: 2.x swaps a model it doesn't know for its default."""
    used = {body.get("model") for body in model.scripted_requests()}
    assert used == {PROVIDERS[provider][0]}, f"OpenCode called {used}"


@pytest.mark.parametrize("provider", list(PROVIDERS))
@pytest.mark.parametrize(("scenario", "reason"), BLOCKED_SCENARIOS)
def test_flagged_call_is_blocked(stack, opencode_home, tmp_path, provider, scenario, reason):
    model, proxy = stack
    model.requests.clear()
    output = run_opencode(opencode_home, tmp_path, scenario)
    check_model(model, provider)
    check_blocked(model, proxy, output, scenario, SHELL_TOOL, reason)


@pytest.mark.parametrize("provider", list(PROVIDERS))
def test_clean_call_runs(stack, opencode_home, tmp_path, provider):
    model, _ = stack
    model.requests.clear()
    output = run_opencode(opencode_home, tmp_path, "clean")
    check_model(model, provider)
    check_ran(model, output)
