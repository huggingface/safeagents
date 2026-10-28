"""Keeps every tool call the guard didn't simply pass, with its full arguments, so it
can be inspected later with `safeagents inspect`.

Arguments can hold secrets, so the file is readable by its owner only.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

DEFAULT_PATH = Path.home() / ".safeagents" / "calls.jsonl"


def read(path: Path = DEFAULT_PATH) -> list[dict]:
    """The recorded calls, oldest first; unreadable lines are skipped."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    entries = []
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and isinstance(entry.get("id"), int):
            entries.append(entry)
    return entries


class Journal:
    """Appends calls to a JSON-lines file, numbering them after those already in it."""

    def __init__(self, path: Path = DEFAULT_PATH) -> None:
        self.path = Path(path)
        self._next: int | None = None

    def append(self, **entry: object) -> int:
        if self._next is None:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            self._next = max((e["id"] for e in read(self.path)), default=0) + 1
        call_id, self._next = self._next, self._next + 1
        line = json.dumps({"id": call_id, "time": time.time(), **entry}, ensure_ascii=False)
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        return call_id
