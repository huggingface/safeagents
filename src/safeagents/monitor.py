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
"""Shows reviewed tool calls in the terminal.

On a terminal: a boxed histogram of verdicts above a box of the latest calls, redrawn
in place. Piped: one plain line per call.
"""

from __future__ import annotations

import json
import time
from collections import Counter, deque
from typing import Self

from rich import box
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

VERDICTS = {
    "passed": ("PASS", "green"),
    "suspicious": ("WARN", "yellow"),
    "flagged": ("FLAG", "dark_orange"),
    "blocked": ("BLOCK", "red"),
}
SUMMARY_KEYS = ("command", "cmd", "url", "query", "file_path", "path", "pattern", "code")
MAX_SUMMARY = 60
# Lines the histogram box takes: borders, the totals line, a gap and one bar per verdict.
STATS_HEIGHT = 2 + 2 + len(VERDICTS)


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


class Monitor:
    """Counts requests and calls and shows each call as it is reviewed."""

    def __init__(self, console: Console | None = None, setup: str = "") -> None:
        self.console = console or Console(stderr=True)
        self.counts: Counter[str] = Counter()
        self.setup = setup
        self.recent: deque[tuple[str, str, str, str, str, str]] = deque(maxlen=200)
        self._live = None
        if self.console.is_terminal:
            self._live = Live(self.render(), console=self.console, auto_refresh=False)

    def __enter__(self) -> Self:
        if self._live:
            self._live.start()
        return self

    def __exit__(self, *exc: object) -> None:
        if self._live:
            self._live.stop()

    def request(self) -> None:
        self.counts["requests"] += 1
        self._refresh()

    def record(
        self,
        tool: str,
        args: str,
        verdict: str,
        reason: str | None,
        signals: list[str],
        judge: str | None,
        call_id: int | None = None,
    ) -> None:
        self.counts["calls"] += 1
        self.counts[verdict] += 1
        why = reason or ", ".join(signals)
        if judge:
            why += f" (judge {judge})"
        call_ref = f"#{call_id}" if call_id else ""
        timestamp = time.strftime("%H:%M:%S")
        what = summary(args)
        self.recent.append((timestamp, call_ref, verdict, tool, what, why))
        if self._live:
            self._refresh()
        else:
            tag = VERDICTS[verdict][0]
            line = f"{timestamp}  {call_ref:<5}  {tag:<5}  {tool:<12}  {what}  {why}".rstrip()
            self.console.print(line, highlight=False, markup=False, soft_wrap=True)

    def render(self) -> Group:
        return Group(self._stats(), self._calls())

    def _stats(self) -> Panel:
        total_requests = self.counts["requests"]
        total_calls = self.counts["calls"]
        text = Text(f"REQUESTS {total_requests:06d}   CALLS {total_calls:06d}\n\n", "bold")
        # Inside the box: 2 borders, 2 padding, then "BLOCK 123456 100% " before the bar.
        bar_width = max(10, self.console.width - 4 - 18)
        for index, (verdict, (tag, color)) in enumerate(VERDICTS.items()):
            count = self.counts[verdict]
            share = count / total_calls if total_calls else 0
            filled = round(bar_width * share)
            text.append(f"{tag:<5} {count:>6} {share:>4.0%} ", color)
            text.append("█" * filled, color)
            text.append("░" * (bar_width - filled), "dim")
            if index < len(VERDICTS) - 1:
                text.append("\n")
        subtitle = Text(f" {self.setup} ", "dim") if self.setup else None
        return Panel(
            text,
            title=Text(" SAFEAGENTS ", "reverse bold"),
            title_align="left",
            subtitle=subtitle,
            subtitle_align="right",
            box=box.DOUBLE,
        )

    def _calls(self) -> Panel:
        max_rows = max(3, self.console.height - STATS_HEIGHT - 3)
        table = Table(box=None, show_header=False, pad_edge=False, expand=True)
        table.add_column(style="dim", no_wrap=True)
        table.add_column(style="bold", no_wrap=True)
        table.add_column(no_wrap=True, width=5)
        table.add_column(style="bold", no_wrap=True, max_width=14, overflow="ellipsis")
        table.add_column(no_wrap=True, overflow="ellipsis", ratio=3)
        table.add_column(no_wrap=True, overflow="ellipsis", ratio=2)
        for timestamp, call_ref, verdict, tool, what, why in list(self.recent)[-max_rows:]:
            tag, color = VERDICTS[verdict]
            what_style = "dim" if verdict == "passed" else ""
            table.add_row(timestamp, call_ref, Text(tag, color), tool, Text(what, what_style), Text(why, color))
        has_call_refs = any(call_ref for _, call_ref, *_ in self.recent)
        hint = Text(" safeagents inspect <#> ", "dim") if has_call_refs else None
        return Panel(
            table,
            title=Text(" CALLS ", "reverse bold"),
            title_align="left",
            subtitle=hint,
            subtitle_align="right",
            box=box.DOUBLE,
            height=max_rows + 2,
        )

    def _refresh(self) -> None:
        if self._live:
            self._live.update(self.render(), refresh=True)
