"""Blocks LLM tool calls the model made up on its own.

A call is blocked when it uses a tool the request never declared, reaches a host
that appears nowhere in the conversation, or trips atif-scan's checks badly enough;
milder atif-scan findings can be sent to a decision model. Handles OpenAI Chat
Completions and Anthropic Messages, full JSON responses and event streams.
"""

from .guard import Blocked, Config, Guard
from .policy import DEFAULT_ALLOWED_HOSTS, Policy, declared_tools, extract_hosts

__all__ = [
    "DEFAULT_ALLOWED_HOSTS",
    "Blocked",
    "Config",
    "Guard",
    "Policy",
    "declared_tools",
    "extract_hosts",
]
