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
    if isinstance(parsed, dict):
        args = next((parsed[k] for k in SUMMARY_KEYS if isinstance(parsed.get(k), str) and parsed[k]), args)
    args = " ".join(args.split())
    return args if len(args) <= MAX_SUMMARY else args[: MAX_SUMMARY - 1] + "~"


class Monitor:
    """Counts requests and calls and shows each call as it is reviewed."""

    def __init__(self, console: Console | None = None, setup: str = "") -> None:
        self.console = console or Console(stderr=True)
        self.counts: Counter[str] = Counter()
        self.setup = setup
        self.recent: deque[tuple[str, str, str, str, str, str]] = deque(maxlen=200)
        self._live = (
            Live(self.render(), console=self.console, auto_refresh=False) if self.console.is_terminal else None
        )

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

    def record(self, tool: str, args: str, verdict: str, reason: str | None, signals: list[str],
               judge: str | None, call_id: int | None = None) -> None:
        self.counts["calls"] += 1
        self.counts[verdict] += 1
        why = reason or ", ".join(signals)
        if judge:
            why += f" (judge {judge})"
        ref = f"#{call_id}" if call_id else ""
        call = (time.strftime("%H:%M:%S"), ref, verdict, tool, summary(args), why)
        self.recent.append(call)
        if self._live:
            self._refresh()
        else:
            at, ref, _, tool, what, why = call
            line = f"{at}  {ref:<5}  {VERDICTS[verdict][0]:<5}  {tool:<12}  {what}  {why}".rstrip()
            self.console.print(line, highlight=False, markup=False, soft_wrap=True)

    def render(self) -> Group:
        return Group(self._stats(), self._calls())

    def _stats(self) -> Panel:
        c = self.counts
        text = Text(f"REQUESTS {c['requests']:06d}   CALLS {c['calls']:06d}\n\n", "bold")
        # Inside the box: 2 borders, 2 padding, then "BLOCK 123456 100% " before the bar.
        width = max(10, self.console.width - 4 - 18)
        for i, (verdict, (tag, color)) in enumerate(VERDICTS.items()):
            n = c[verdict]
            share = n / c["calls"] if c["calls"] else 0
            filled = round(width * share)
            text.append(f"{tag:<5} {n:>6} {share:>4.0%} ", color)
            text.append("█" * filled, color)
            text.append("░" * (width - filled), "dim")
            if i < len(VERDICTS) - 1:
                text.append("\n")
        return Panel(text, title=Text(" SAFEAGENTS ", "reverse bold"), title_align="left",
                     subtitle=Text(f" {self.setup} ", "dim") if self.setup else None, subtitle_align="right",
                     box=box.DOUBLE)

    def _calls(self) -> Panel:
        rows = max(3, self.console.height - STATS_HEIGHT - 3)
        table = Table(box=None, show_header=False, pad_edge=False, expand=True)
        table.add_column(style="dim", no_wrap=True)
        table.add_column(style="bold", no_wrap=True)
        table.add_column(no_wrap=True, width=5)
        table.add_column(style="bold", no_wrap=True, max_width=14, overflow="ellipsis")
        table.add_column(no_wrap=True, overflow="ellipsis", ratio=3)
        table.add_column(no_wrap=True, overflow="ellipsis", ratio=2)
        for at, ref, verdict, tool, call, why in list(self.recent)[-rows:]:
            tag, color = VERDICTS[verdict]
            table.add_row(at, ref, Text(tag, color), tool, Text(call, "dim" if verdict == "passed" else ""),
                          Text(why, color))
        hint = Text(" safeagents inspect <#> ", "dim") if any(r[1] for r in self.recent) else None
        return Panel(table, title=Text(" CALLS ", "reverse bold"), title_align="left", subtitle=hint,
                     subtitle_align="right", box=box.DOUBLE, height=rows + 2)

    def _refresh(self) -> None:
        if self._live:
            self._live.update(self.render(), refresh=True)
