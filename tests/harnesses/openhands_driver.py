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
"""Runs one OpenHands SDK conversation, set up as in docs/openhands.md.

test_openhands.py runs this with the interpreter of a separate environment that has
openhands-sdk and openhands-tools installed, so safeagents' own environment doesn't
need them. It prints every event of the conversation as one JSON line.

    python openhands_driver.py --route anthropic --base-url URL --workdir DIR --prompt TEXT
"""

from __future__ import annotations

import argparse
import os

# Keep LiteLLM on its bundled model list instead of downloading one at import time.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
os.environ.setdefault("OPENHANDS_SUPPRESS_BANNER", "1")

from openhands.sdk import LLM, Agent, Conversation, Tool
from openhands.tools.terminal import TerminalTool
from pydantic import SecretStr

# The models docs/openhands.md uses. The fake model behind the proxy ignores the name,
# but the SDK doesn't: `gpt-4.1` is one LiteLLM lists for the Responses API, so the
# OpenAI route only stays on Chat Completions because of `api_mode="chat"`.
ANTHROPIC_MODEL = "anthropic/claude-sonnet-4-5-20250929"
OPENAI_MODEL = "openai/gpt-4.1"


def build_llm(route: str, base_url: str) -> LLM:
    """The agent's LLM, pointed at `base_url` (the proxy, without /v1) as in docs/openhands.md."""
    api_key = SecretStr("test-key")
    if route == "anthropic":
        return LLM(model=ANTHROPIC_MODEL, base_url=base_url, api_key=api_key)
    if route == "openai":
        return LLM(model=OPENAI_MODEL, base_url=base_url + "/v1", api_key=api_key, api_mode="chat")
    raise ValueError(f"unknown route {route!r}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--route", choices=["anthropic", "openai"], required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--workdir", required=True)
    parser.add_argument("--prompt", required=True)
    args = parser.parse_args()

    agent = Agent(llm=build_llm(args.route, args.base_url), tools=[Tool(name=TerminalTool.name)])
    conversation = Conversation(agent=agent, workspace=args.workdir, visualizer=None, max_iteration_per_run=10)
    try:
        conversation.send_message(args.prompt)
        conversation.run()
        for event in conversation.state.events:
            print(event.model_dump_json(), flush=True)
    finally:
        conversation.close()


if __name__ == "__main__":
    main()
