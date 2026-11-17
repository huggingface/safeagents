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
"""Blocks calls the guarded agent makes against the proxy that guards it.

The proxy guards the agent's tool calls, but by default nothing stops the agent from
turning the proxy off, putting it in log-only mode, deleting or editing the journal
(the only record of a blocked call), redirecting the harness away from the proxy, or
dropping an atif-scan allowance that excuses a detector. These checks run before the
policy and scanner, on the arguments both raw and JSON-decoded, and are not excusable
by a plugin allowance: an allowance only affects atif-scan findings, this runs earlier
and independently.

The checks are deliberately coarse. A guarded coding agent has no legitimate reason to
touch the proxy's own control surface, so matching its name, state directory, the
harness base-URL env vars, or an atif-scan `Allowance` is treated as an attempt even
when it could be benign. `Config(self_protection=False)` / `--no-self-protection` turns
them off for a run that genuinely needs them.
"""

from __future__ import annotations

import json
import re

# `safeagents start`/`stop` change which proxies run; a `safeagents` invocation carrying
# a control-weakening flag restarts it degraded. `inspect`/`scrub`/`publish` are benign.
_CLI_CONTROL = re.compile(r"\bsafeagents\b(?:\s+\S+)*\s+(?:start|stop)\b", re.IGNORECASE)
_CLI_WEAKEN = re.compile(r"\bsafeagents\b.*?--(?:log-only|no-journal|no-scan|no-self-protection)\b", re.IGNORECASE)
# Killing the proxy process, in either order (`pkill -f safeagents`, `safeagents` pid to `kill`).
_PROCESS_KILL = re.compile(
    r"\b(?:pkill|killall|kill)\b[^\"]*safeagents|safeagents[^\"]*\b(?:pkill|killall)\b", re.IGNORECASE
)
# Reading the proxy process's environment or memory (`/proc/$(pgrep -f safeagents)/environ`),
# which holds `SAFEAGENTS_JUDGE_API_KEY` and whatever the shell that started it exported.
# atif-scan does not flag it: its harness-secret check is about the agent's own process.
_PROCESS_ENV = re.compile(r"(?s)(?=.*\bsafeagents\b).*/proc/[^\n\"']{0,120}?/(?:environ|mem)\b")
# The proxy's own state directory and journal (run locks, descriptors, calls.jsonl).
_STATE_DIR = re.compile(r"[~/.\w]*\.safeagents\b", re.IGNORECASE)
# Redirecting the harness away from the proxy by setting its base-URL environment variable.
_BASE_URL_ENV = re.compile(
    r"\b(?:ANTHROPIC_BASE_URL|ANTHROPIC_API_URL|OPENAI_BASE_URL|OPENAI_API_BASE|OPENAI_BASE"
    r"|GOOGLE_GEMINI_BASE_URL|GEMINI_BASE_URL)\b\s*=|"
    r"\bexport\s+(?:ANTHROPIC_BASE_URL|OPENAI_BASE_URL|OPENAI_API_BASE|GOOGLE_GEMINI_BASE_URL|GEMINI_BASE_URL)\b",
    re.IGNORECASE,
)
# Harness config files that carry the base URL / provider, so editing them redirects the harness.
_HARNESS_SETTINGS = re.compile(r"\.claude/settings(?:\.local)?\.json|\.codex/config\.toml", re.IGNORECASE)
_BASE_URL_KEY = re.compile(r"base[_-]?url|baseURL|model_provider|openai_base_url", re.IGNORECASE)
# A written file that defines an atif-scan allowance is a configuration change: it would
# excuse a detector the next time the proxy loads plugins.
_ALLOWANCE_WRITE = re.compile(r"atif_scan|atif-scan").search

_RULES = (
    (_CLI_CONTROL.search, "starts or stops the safeagents proxy"),
    (_CLI_WEAKEN.search, "restarts the safeagents proxy with its checks weakened"),
    (_PROCESS_KILL.search, "kills the safeagents proxy process"),
    (_PROCESS_ENV.search, "reads the safeagents proxy's process environment"),
    (_STATE_DIR.search, "touches the safeagents state directory or journal"),
    (_BASE_URL_ENV.search, "redirects the harness base URL away from the proxy"),
)


def _decoded(args: str) -> str:
    """The arguments as the tool decodes them: JSON escapes (`\\u0073afeagents`) resolved."""
    try:
        return json.dumps(json.loads(args), ensure_ascii=False)
    except ValueError:
        return args


def self_protection_reason(name: str, args: str) -> str | None:
    """Why a call attacks the proxy guarding it, if it does; otherwise None.

    `args` is the call's raw JSON arguments, so a path or command in any field is seen.
    Both the raw text and its decoded form are checked: the tool runs the decoded one.
    """
    raw = args or ""
    for text in dict.fromkeys((raw, _decoded(raw))):
        reason = _reason(text)
        if reason is not None:
            return reason
    return None


def _reason(text: str) -> str | None:
    for matches, why in _RULES:
        if matches(text):
            return f"self-protection: {why}"
    # Editing a harness settings file to point it elsewhere.
    if _HARNESS_SETTINGS.search(text) and _BASE_URL_KEY.search(text):
        return "self-protection: edits a harness settings file to redirect it away from the proxy"
    # Writing an atif-scan allowance (a file that imports atif-scan and defines an Allowance).
    if _ALLOWANCE_WRITE(text) and re.search(r"\bAllowance\b", text):
        return "self-protection: writes an atif-scan allowance that would excuse a detector"
    return None
