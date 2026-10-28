"""Checks that need only the request: the tools it declared and the hosts it mentions."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable

# Hosts that may appear in tool calls even if the conversation never mentions them.
DEFAULT_ALLOWED_HOSTS = (
    "localhost",
    "127.0.0.1",
    "github.com",
    "githubusercontent.com",
    "pypi.org",
    "pythonhosted.org",
    "huggingface.co",
    "hf.co",
    "npmjs.org",
    "crates.io",
)

_AUTHORITY = re.compile(r"://([^\s/?#\"'\\`)\]>]*)")
_HOST = re.compile(r"[a-z0-9.-]*")
_IPV4 = re.compile(r"(?<![\d.])\d{1,3}(?:\.\d{1,3}){3}(?![\d.])")


def declared_tools(request: dict) -> set[str]:
    """Tool names in the request's `tools`: OpenAI `function.name`, Anthropic `name`."""
    names = set()
    for tool in request.get("tools") or []:
        if not isinstance(tool, dict):
            continue
        function = tool.get("function")
        name = function.get("name") if isinstance(function, dict) else tool.get("name")
        if isinstance(name, str):
            names.add(name)
    return names


def extract_hosts(text: str) -> list[str]:
    """Lowercased hosts in `text`: the host of `scheme://` URLs and bare IPv4 literals."""
    text = text.lower()
    hosts = set()
    for authority in _AUTHORITY.findall(text):
        host = _HOST.match(authority.rsplit("@", 1)[-1]).group()
        if host:
            hosts.add(host)
    for ip in _IPV4.findall(text):
        if all(int(part) <= 255 for part in ip.split(".")):
            hosts.add(ip)
    return sorted(hosts)


class Policy:
    """What a request allows its response to call."""

    def __init__(self, request: dict, allowed_hosts: Iterable[str] = DEFAULT_ALLOWED_HOSTS):
        self.declared = declared_tools(request)
        # The request without the model's own turns: a host the model wrote earlier
        # is no more sourced than one it writes now.
        context = dict(request)
        if isinstance(context.get("messages"), list):
            context["messages"] = [
                m for m in context["messages"] if not (isinstance(m, dict) and m.get("role") == "assistant")
            ]
        self.context = json.dumps(context, ensure_ascii=False).lower()
        self.allowed_hosts = [h.lower() for h in allowed_hosts]

    def host_ok(self, host: str) -> bool:
        return host in self.context or any(host == a or host.endswith("." + a) for a in self.allowed_hosts)

    def check(self, name: str, args: str) -> str | None:
        """Why a call to `name` with raw `args` should be blocked, if it should."""
        if name not in self.declared:
            return "undeclared tool"
        for host in extract_hosts(args):
            if not self.host_ok(host):
                return f"host `{host}` appears nowhere in the conversation"
        return None
