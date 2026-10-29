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
"""Runs the real Qwen Code CLI through safeagents, set up as in docs/qwen-code.md.

Skipped unless `qwen` is installed (or `QWEN_BIN` points at it).
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

QWEN_BIN = os.environ.get("QWEN_BIN") or shutil.which("qwen")
pytestmark = pytest.mark.skipif(QWEN_BIN is None, reason="qwen is not installed")

SHELL_TOOL = "run_shell_command"
SHELL_ARG = "command"
MODEL_ID = "fake-model"
# Qwen Code auth type -> its `modelProviders` entry, minus the base URL, and whether
# the base URL takes /v1, as in docs/qwen-code.md.
AUTH_TYPES = {
    "openai": ({"envKey": "OPENAI_API_KEY", "wireApi": "chat-completions"}, True),
    "anthropic": ({"envKey": "ANTHROPIC_API_KEY"}, False),
}
# Environment variables that would override the settings file or leak real credentials.
ISOLATED_PREFIXES = ("OPENAI_", "ANTHROPIC_", "DASHSCOPE_", "QWEN_")


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    with FakeModel() as model, running_proxy(model.url, tmp_path_factory.mktemp("proxy")) as proxy:
        yield model, proxy


@pytest.fixture
def qwen_home(stack, tmp_path, auth_type) -> Path:
    """A home directory whose ~/.qwen/settings.json points `auth_type` at the proxy."""
    _, proxy = stack
    provider, with_v1 = AUTH_TYPES[auth_type]
    base_url = proxy.url + "/v1" if with_v1 else proxy.url
    settings = {
        "modelProviders": {auth_type: [{"id": MODEL_ID, "baseUrl": base_url, **provider}]},
        "security": {"auth": {"selectedType": auth_type}},
        "model": {"name": MODEL_ID},
        "general": {"enableAutoUpdate": False},
        "privacy": {"usageStatisticsEnabled": False},
        "telemetry": {"enabled": False},
        # Background memory extraction resends the conversation to the model after the
        # turn; keep each run to the requests of the main loop.
        "memory": {"enableManagedAutoMemory": False, "enableManagedAutoDream": False},
    }
    home = tmp_path / "home"
    settings_dir = home / ".qwen"
    settings_dir.mkdir(parents=True)
    (settings_dir / "settings.json").write_text(json.dumps(settings, indent=2))
    return home


def run_qwen(qwen_home: Path, workdir: Path, scenario: str) -> str:
    """Runs one headless Qwen Code prompt and returns its JSON output."""
    env = {}
    for name, value in os.environ.items():
        if not name.startswith(ISOLATED_PREFIXES):
            env[name] = value
    env.update(
        {
            "HOME": str(qwen_home),
            "OPENAI_API_KEY": "test-key",
            "ANTHROPIC_API_KEY": "test-key",
            "QWEN_CODE_SKIP_UPDATE_CHECK_ONCE": "true",
            "QWEN_CODE_SUPPRESS_YOLO_WARNING": "1",
        }
    )
    command = [
        QWEN_BIN,
        "--approval-mode",
        "yolo",
        "--output-format",
        "json",
        scenario_prompt(scenario, tool=SHELL_TOOL, arg=SHELL_ARG),
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
    assert completed.returncode == 0, f"qwen failed:\n{completed.stderr}"
    return completed.stdout


@pytest.mark.parametrize("auth_type", list(AUTH_TYPES))
@pytest.mark.parametrize(("scenario", "reason"), BLOCKED_SCENARIOS)
def test_flagged_call_is_blocked(stack, qwen_home, tmp_path, auth_type, scenario, reason):
    model, proxy = stack
    model.requests.clear()
    output = run_qwen(qwen_home, tmp_path, scenario)
    check_blocked(model, proxy, output, scenario, SHELL_TOOL, reason)


@pytest.mark.parametrize("auth_type", list(AUTH_TYPES))
def test_clean_call_runs(stack, qwen_home, tmp_path, auth_type):
    model, _ = stack
    model.requests.clear()
    output = run_qwen(qwen_home, tmp_path, "clean")
    check_ran(model, output)
