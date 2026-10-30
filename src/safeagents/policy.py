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
"""Checks that need only the request: the tools it declared and the hosts it mentions."""

from __future__ import annotations

import json
from collections.abc import Iterable

from .constants import AUTHORITY_PATTERN, DEFAULT_ALLOWED_HOSTS, HOST_PATTERN, IPV4_PATTERN


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
    for authority in AUTHORITY_PATTERN.findall(text):
        host_and_port = authority.rsplit("@", 1)[-1]
        host = HOST_PATTERN.match(host_and_port).group()
        if host:
            hosts.add(host)
    for ip_address in IPV4_PATTERN.findall(text):
        if all(int(octet) <= 255 for octet in ip_address.split(".")):
            hosts.add(ip_address)
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
                message
                for message in context["messages"]
                if not (isinstance(message, dict) and message.get("role") == "assistant")
            ]
        self.context = json.dumps(context, ensure_ascii=False).lower()
        self.allowed_hosts = [host.lower() for host in allowed_hosts]

    def check(self, name: str, args: str) -> str | None:
        """Why a call to `name` with raw `args` should be blocked, if it should."""
        if name not in self.declared:
            return "undeclared tool"
        for host in extract_hosts(args):
            if host in self.context:
                continue
            if any(host == allowed or host.endswith("." + allowed) for allowed in self.allowed_hosts):
                continue
            return f"host `{host}` appears nowhere in the conversation"
        return None
