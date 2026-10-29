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
"""Decides, call by call, whether a response's tool calls may run.

1. Undeclared tools and hosts from nowhere are blocked outright.
2. atif-scan findings at or above `block_severity` are blocked too.
3. Milder findings make a call suspicious: the judge, if there is one, decides.
   Without one, suspicious calls are only logged.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

from atif_scan import Severity

from .journal import Journal
from .judge import Judge, JudgeError, task_text
from .monitor import Monitor
from .policy import DEFAULT_ALLOWED_HOSTS, Policy
from .scan import Scanner, Signal, history, recent

logger = logging.getLogger("safeagents")


@dataclass
class Config:
    # Replace flagged tool calls instead of only logging them.
    block: bool = True
    # Hosts tool calls may reach even if the conversation never mentions them.
    allowed_hosts: Sequence[str] = DEFAULT_ALLOWED_HOSTS
    # None turns atif-scan off.
    scanner: Scanner | None = field(default_factory=Scanner)
    block_severity: Severity = Severity.HIGH
    judge: Judge | None = None
    # Block suspicious calls when the judge can't be reached or gives no verdict.
    judge_fail_closed: bool = False
    # Shows reviewed calls in the terminal; without one, flagged calls are logged.
    monitor: Monitor | None = None
    # Keeps calls that weren't simply passed, for `safeagents inspect`.
    journal: Journal | None = None


@dataclass(frozen=True)
class Blocked:
    """A tool call the guard flagged."""

    name: str
    reason: str

    def notice(self) -> str:
        return f"[safeagents] blocked call to `{self.name}`: {self.reason}"


@dataclass
class Review:
    """What the guard found out about one call."""

    reason: str | None = None
    signals: list[Signal] = field(default_factory=list)
    # The judge's answer as "allow: why", "block: why" or "error: why".
    judge: str | None = None


class Guard:
    """Reviews the tool calls of one response to one request."""

    def __init__(self, request: dict, config: Config) -> None:
        self.request = request
        self.config = config
        self.policy = Policy(request, config.allowed_hosts)
        self.flagged: list[Blocked] = []
        self._calls: list[tuple[str, str]] = []
        self._steps: list[dict] | None = None

    async def allow(self, name: str, args: str) -> bool:
        """Reviews a complete call; True if it may run (always, in log-only mode)."""
        self._calls.append((name, args))
        policy_reason = self.policy.check(name, args)
        if policy_reason is not None:
            review = Review(policy_reason)
        else:
            review = await self._scan(name, args)

        if review.reason is not None:
            self.flagged.append(Blocked(name, review.reason))
            verdict = "blocked" if self.config.block else "flagged"
        elif review.signals:
            verdict = "suspicious"
        else:
            verdict = "passed"
        signal_names = [str(signal) for signal in review.signals]

        call_id = None
        journal = self.config.journal
        if verdict != "passed" and journal is not None:
            journal_signals = [
                {"id": signal.id, "severity": signal.severity.name.lower(), "title": signal.title}
                for signal in review.signals
            ]
            call_id = journal.append(
                verdict=verdict,
                tool=name,
                arguments=args,
                reason=review.reason,
                signals=journal_signals,
                judge=review.judge,
                model=str(self.request.get("model") or ""),
                task=task_text(self.request),
            )

        monitor = self.config.monitor
        if monitor is not None:
            monitor.record(name, args, verdict, review.reason, signal_names, review.judge, call_id)
        elif verdict != "passed":
            why = review.reason or ", ".join(signal_names)
            judge_note = f" (judge {review.judge})" if review.judge else ""
            call_ref = f" [#{call_id}]" if call_id else ""
            logger.warning("[safeagents] %s call to `%s`: %s%s%s", verdict, name, why, judge_note, call_ref)
        return review.reason is None or not self.config.block

    async def _scan(self, name: str, args: str) -> Review:
        scanner = self.config.scanner
        if scanner is None:
            return Review()
        if self._steps is None:
            self._steps = recent(history(self.request))
        signals = scanner.signals(self._steps, self._calls)
        severe_signals = [signal for signal in signals if signal.severity >= self.config.block_severity]
        if severe_signals:
            return Review("atif-scan: " + ", ".join(str(signal) for signal in severe_signals), signals)
        if not signals:
            return Review()
        return await self._judge(name, args, signals)

    async def _judge(self, name: str, args: str, signals: list[Signal]) -> Review:
        judge = self.config.judge
        if judge is None:
            return Review(None, signals)
        findings = ", ".join(str(signal) for signal in signals)
        try:
            block, why = await judge.review(self.request, name, args, signals)
        except JudgeError as e:
            reason = f"{findings}; judge unavailable" if self.config.judge_fail_closed else None
            return Review(reason, signals, f"error: {e}")
        if block:
            return Review(f"{findings}; judge: {why}", signals, f"block: {why}")
        return Review(None, signals, f"allow: {why}")
