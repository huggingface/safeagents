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
"""Small helpers shared across the package."""

from __future__ import annotations

import json
import time

from .constants import MAX_SUMMARY, SUMMARY_KEYS, TIME_FORMAT


def compact_json(value: object) -> str:
    """`value` as JSON without whitespace, non-ASCII kept as is."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def format_time(timestamp: float) -> str:
    """A Unix timestamp in local time, e.g. 2026-01-31 12:00:00."""
    return time.strftime(TIME_FORMAT, time.localtime(timestamp))


def listen_address(value: str) -> str:
    """A bare port listens on this machine only."""
    if value.isdigit():
        return f"127.0.0.1:{value}"
    return value


def content_text(content: object) -> str:
    """Text of a message `content`: a string, or the text parts of a list."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if not isinstance(part, dict) or part.get("type") not in ("text", "input_text"):
                continue
            if isinstance(part.get("text"), str):
                parts.append(part["text"])
        return "\n".join(parts)
    return ""


def summary(args: str) -> str:
    """The one argument that says what a call does, else the raw arguments, on one line."""
    try:
        parsed = json.loads(args)
    except ValueError:
        parsed = None
    text = args
    if isinstance(parsed, dict):
        for key in SUMMARY_KEYS:
            value = parsed.get(key)
            if isinstance(value, str) and value:
                text = value
                break
    text = " ".join(text.split())
    if len(text) > MAX_SUMMARY:
        text = text[: MAX_SUMMARY - 1] + "~"
    return text
