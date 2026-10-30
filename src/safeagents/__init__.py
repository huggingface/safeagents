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
"""Blocks LLM tool calls the model made up on its own.

A call is blocked when it uses a tool the request never declared, reaches a host
that appears nowhere in the conversation, or trips atif-scan's checks badly enough;
milder atif-scan findings can be sent to a decision model. Handles OpenAI Chat
Completions and Anthropic Messages, full JSON responses and event streams.
"""

from .constants import DEFAULT_ALLOWED_HOSTS
from .guard import Blocked, Config, Guard
from .policy import Policy, declared_tools, extract_hosts

__all__ = [
    "DEFAULT_ALLOWED_HOSTS",
    "Blocked",
    "Config",
    "Guard",
    "Policy",
    "declared_tools",
    "extract_hosts",
]
