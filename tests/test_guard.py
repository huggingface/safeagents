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
import asyncio
import time

from conftest import bash_request

from safeagents import Config, Guard


class BusyScanner:
    """A stand-in for atif-scan: `signals` is synchronous and CPU-bound (holds the GIL)."""

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds

    def signals(self, steps, calls):
        deadline = time.perf_counter() + self.seconds
        while time.perf_counter() < deadline:
            pass
        return []


def test_a_cpu_bound_scan_does_not_freeze_the_event_loop():
    scan_seconds = 0.4

    async def go() -> tuple[bool, float]:
        guard = Guard(bash_request("hi"), Config(scanner=BusyScanner(scan_seconds)))
        review = asyncio.ensure_future(guard.allow("bash", '{"command": "ls"}'))
        # Another request's work: a ticker that should keep running during the scan.
        gaps, last = [], time.perf_counter()
        while not review.done():
            await asyncio.sleep(0.005)
            now = time.perf_counter()
            gaps.append(now - last)
            last = now
        return await review, max(gaps)

    allowed, worst_gap = asyncio.run(go())
    assert allowed  # no findings
    # On the loop, the ticker would stall for the whole scan. In a worker thread the
    # interpreter hands the GIL back every switch interval (5 ms by default), so the
    # loop keeps serving; it does not make the scan itself parallel.
    assert worst_gap < scan_seconds / 4
