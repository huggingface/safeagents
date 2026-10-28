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

log = logging.getLogger("safeagents")


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

    @property
    def block(self) -> bool:
        return self.config.block

    async def allow(self, name: str, args: str) -> bool:
        """Reviews a complete call; True if it may run (always, in log-only mode)."""
        self._calls.append((name, args))
        reason = self.policy.check(name, args)
        review = Review(reason) if reason else await self._scan(name, args)
        if review.reason is not None:
            self.flagged.append(Blocked(name, review.reason))
            verdict = "blocked" if self.block else "flagged"
        else:
            verdict = "suspicious" if review.signals else "passed"
        signals = [str(s) for s in review.signals]
        call_id = None
        if verdict != "passed" and (journal := self.config.journal):
            call_id = journal.append(
                verdict=verdict, tool=name, arguments=args, reason=review.reason,
                signals=[{"id": s.id, "severity": s.severity.name.lower(), "title": s.title} for s in review.signals],
                judge=review.judge, model=str(self.request.get("model") or ""), task=task_text(self.request),
            )
        if monitor := self.config.monitor:
            monitor.record(name, args, verdict, review.reason, signals, review.judge, call_id)
        elif verdict != "passed":
            why = review.reason or ", ".join(signals)
            judge = f" (judge {review.judge})" if review.judge else ""
            ref = f" [#{call_id}]" if call_id else ""
            log.warning("[safeagents] %s call to `%s`: %s%s%s", verdict, name, why, judge, ref)
        return review.reason is None or not self.block

    async def _scan(self, name: str, args: str) -> Review:
        scanner = self.config.scanner
        if scanner is None:
            return Review()
        if self._steps is None:
            self._steps = recent(history(self.request))
        signals = scanner.signals(self._steps, self._calls)
        if severe := [s for s in signals if s.severity >= self.config.block_severity]:
            return Review("atif-scan: " + ", ".join(map(str, severe)), signals)
        if not signals:
            return Review()
        return await self._judge(name, args, signals)

    async def _judge(self, name: str, args: str, signals: list[Signal]) -> Review:
        found = ", ".join(map(str, signals))
        judge = self.config.judge
        if judge is None:
            return Review(None, signals)
        try:
            block, why = await judge.review(self.request, name, args, signals)
        except JudgeError as e:
            reason = f"{found}; judge unavailable" if self.config.judge_fail_closed else None
            return Review(reason, signals, f"error: {e}")
        return Review(f"{found}; judge: {why}" if block else None, signals, f"{'block' if block else 'allow'}: {why}")
