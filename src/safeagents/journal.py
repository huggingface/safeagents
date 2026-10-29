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
        self._next_id: int | None = None

    def append(self, **entry: object) -> int:
        if self._next_id is None:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            existing_ids = [existing["id"] for existing in read(self.path)]
            self._next_id = max(existing_ids, default=0) + 1
        call_id = self._next_id
        self._next_id += 1
        line = json.dumps({"id": call_id, "time": time.time(), **entry}, ensure_ascii=False)
        file_descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(file_descriptor, "a", encoding="utf-8") as file:
            file.write(line + "\n")
        return call_id
