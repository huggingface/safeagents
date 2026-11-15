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
"""Opt-in, best-effort delivery of journal-shaped observations."""

from __future__ import annotations

import copy
import importlib.util
import inspect
import logging
import sys
import threading
import uuid
from collections.abc import Callable, Sequence
from queue import Empty, Full, Queue

Sink = Callable[[dict], None]
QUEUE_SIZE = 16
CALL_TIMEOUT = 0.25
logger = logging.getLogger("safeagents")


def _validate(sink: Sink) -> None:
    if not callable(sink) or inspect.iscoroutinefunction(sink) or inspect.iscoroutinefunction(sink.__call__):
        raise TypeError("a sink must be a synchronous callable accepting one dict")


def load_sink(spec: str) -> Sink:
    """Load MODULE:FACTORY or path/to/file.py:FACTORY, like --plugin."""
    module, _, factory = spec.rpartition(":")
    if not module or not factory:
        raise ValueError("sink must be MODULE:FACTORY")
    if module.endswith(".py"):
        name = "_safeagents_sink_" + uuid.uuid4().hex
        file_spec = importlib.util.spec_from_file_location(name, module)
        if file_spec is None or file_spec.loader is None:
            raise ValueError("cannot load sink module")
        loaded = importlib.util.module_from_spec(file_spec)
        sys.modules[name] = loaded
        try:
            file_spec.loader.exec_module(loaded)
        finally:
            sys.modules.pop(name, None)
    else:
        loaded = importlib.import_module(module)
    create = getattr(loaded, factory)
    if not callable(create) or inspect.iscoroutinefunction(create):
        raise TypeError("a sink factory must be synchronous")
    sink = create()
    if inspect.iscoroutine(sink):
        sink.close()
    _validate(sink)
    return sink


class _Worker:
    def __init__(self, sink: Sink) -> None:
        self.sink = sink
        self.queue: Queue[dict] = Queue(maxsize=QUEUE_SIZE)
        self.closed = threading.Event()
        self.dropped = threading.Event()
        threading.Thread(target=self._run, name="safeagents-sink", daemon=True).start()

    def submit(self, entry: dict) -> None:
        if self.closed.is_set():
            return
        try:
            self.queue.put_nowait(copy.deepcopy(entry))
        except Full:
            self.dropped.set()

    def _report_drops(self) -> None:
        if self.dropped.is_set():
            self.dropped.clear()
            logger.warning("[safeagents] sink queue full; notifications dropped")

    def _invoke(self, entry: dict, finished: threading.Event) -> None:
        try:
            result = self.sink(entry)
            if inspect.isawaitable(result):
                if inspect.iscoroutine(result):
                    result.close()
                raise TypeError("sink returned an awaitable")
        except BaseException:  # noqa: BLE001 - isolate callback control flow from threading.excepthook
            # Exception text can contain the payload or a delivery credential.
            logger.warning("[safeagents] sink failed; notification not retried")
        finally:
            finished.set()

    def _run(self) -> None:
        while not self.closed.is_set():
            self._report_drops()
            try:
                entry = self.queue.get(timeout=0.1)
            except Empty:
                continue
            finished = threading.Event()
            threading.Thread(
                target=self._invoke, args=(entry, finished), name="safeagents-sink-call", daemon=True
            ).start()
            if not finished.wait(CALL_TIMEOUT):
                logger.warning("[safeagents] sink exceeded its callback deadline; waiting off the response path")
                # Python cannot kill a callback thread. Keep just this one in
                # flight, rather than leaking a thread on each notification.
                while not finished.wait(0.1):
                    self._report_drops()
                    if self.closed.is_set():
                        return


class SinkDispatcher:
    def __init__(self, sinks: Sequence[Sink]) -> None:
        for sink in sinks:
            _validate(sink)
        self._workers = [_Worker(sink) for sink in sinks]

    def publish(self, entry: dict) -> None:
        for worker in self._workers:
            worker.submit(entry)

    def close(self) -> None:
        """Stop accepting notifications; pending work is not flushed."""
        for worker in self._workers:
            worker.closed.set()
            while True:
                try:
                    worker.queue.get_nowait()
                except Empty:
                    break
