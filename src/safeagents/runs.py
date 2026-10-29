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
"""Proxies running in the background: `safeagents start`, `safeagents ls` and `safeagents stop`.

Each proxy has a number N and three files in ~/.safeagents/run: N.json describes it, N.log
holds its output, and N.lock is locked by the proxy for as long as it runs. The OS drops
the lock when the process dies, however it dies, so a held lock is what "running" means.
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import IO

from .cli import _listen

# File locking has no portable API in the standard library: Windows has msvcrt.locking,
# everything else has fcntl.flock. Each module only exists on its own platform.
if os.name == "nt":
    import msvcrt
else:
    import fcntl

DEFAULT_LISTEN = "127.0.0.1:8787"
START_TIMEOUT = 30.0
STOP_TIMEOUT = 5.0
LOG_TAIL = 20

START_USAGE = "safeagents start URL [PORT_OR_ADDRESS] [proxy options]"


def run_dir() -> Path:
    return Path.home() / ".safeagents" / "run"


def hold_lock(path: Path) -> IO:
    """Locks `path` for as long as the returned file stays open."""
    file = open(path, "a")  # noqa: SIM115 (stays open, and locked, for the proxy's lifetime)
    if not _try_lock(file):
        sys.exit(f"safeagents: {path} is locked by another proxy")
    return file


def start(argv: list[str]) -> None:
    """Starts a proxy in the background and prints its number once it accepts connections."""
    if not argv or argv[0] in ("-h", "--help"):
        print(f"usage: {START_USAGE}\n\nStarts the proxy in the background. Options are those of `safeagents URL`.")
        return
    upstream, options = argv[0], argv[1:]
    listen = DEFAULT_LISTEN
    if options and not options[0].startswith("-"):
        listen = _listen(options.pop(0))
    if _accepts(listen):
        sys.exit(f"safeagents: {listen} is already in use")

    with _run_lock() as directory:
        number = 1
        while _running(directory / f"{number}.lock"):
            number += 1
        lock = directory / f"{number}.lock"
        log = directory / f"{number}.log"
        log_file = os.fdopen(os.open(log, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb")
        if os.name == "nt":
            detach = {"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
        else:
            detach = {"start_new_session": True}
        with log_file:
            process = subprocess.Popen(
                [sys.executable, "-m", "safeagents.cli", upstream, listen, *options],
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=log_file,
                env={**os.environ, "SAFEAGENTS_LOCK": str(lock)},
                **detach,
            )
        record = {
            "id": number,
            "pid": process.pid,
            "upstream": upstream,
            "listen": listen,
            "options": options,
            "started": time.time(),
            "log": str(log),
        }
        (directory / f"{number}.json").write_text(json.dumps(record), encoding="utf-8")

        deadline = time.monotonic() + START_TIMEOUT
        while not (_accepts(listen) and _running(lock)):
            if process.poll() is not None or time.monotonic() > deadline:
                if process.poll() is None:
                    process.kill()
                _forget(directory, number)
                tail = log.read_text(encoding="utf-8", errors="replace").splitlines()[-LOG_TAIL:]
                sys.exit("\n".join(["safeagents: the proxy didn't start", *tail]))
            time.sleep(0.1)
    print(f"{number}  http://{listen} -> {upstream}  (log: {log})")


def ls(argv: list[str]) -> None:
    """Lists the proxies running in the background, forgetting those that died."""
    if argv:
        sys.exit("usage: safeagents ls")
    with _run_lock() as directory:
        records = []
        for path in directory.glob("*.json"):
            record = _read(path)
            if record and _running(directory / f"{record['id']}.lock"):
                records.append(record)
            else:
                _forget(directory, path.stem)
    if not records:
        print("no proxies running")
    for record in sorted(records, key=lambda record: record["id"]):
        started = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(record["started"]))
        print(
            f"{record['id']:<3} pid {record['pid']:<7} http://{record['listen']} -> {record['upstream']}  "
            f"started {started}  {' '.join(record['options'])}".rstrip()
        )


def stop(argv: list[str]) -> None:
    """Stops a proxy started with `safeagents start`."""
    if len(argv) != 1 or not argv[0].isdigit():
        sys.exit("usage: safeagents stop ID")
    number = int(argv[0])
    with _run_lock() as directory:
        record = _read(directory / f"{number}.json")
        lock = directory / f"{number}.lock"
        if record is None or not _running(lock):
            _forget(directory, number)
            sys.exit(f"safeagents: no proxy {number} is running")
        os.kill(record["pid"], signal.SIGTERM)
        if not _wait_stopped(lock) and hasattr(signal, "SIGKILL"):
            os.kill(record["pid"], signal.SIGKILL)
            _wait_stopped(lock)
        _forget(directory, number)
    print(f"stopped {number}  http://{record['listen']} -> {record['upstream']}")


@contextlib.contextmanager
def _run_lock() -> Iterator[Path]:
    """Keeps other start, ls and stop commands out while this one changes the run directory."""
    directory = run_dir()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    with open(directory / ".lock", "a") as file:
        if os.name == "nt":
            msvcrt.locking(file.fileno(), msvcrt.LK_LOCK, 1)
        else:
            fcntl.flock(file.fileno(), fcntl.LOCK_EX)
        yield directory


def _try_lock(file: IO) -> bool:
    try:
        if os.name == "nt":
            msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _running(lock: Path) -> bool:
    """True while a proxy holds `lock`."""
    if not lock.exists():
        return False
    with open(lock, "a") as file:
        if not _try_lock(file):
            return True
        if os.name == "nt":
            msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
    return False


def _wait_stopped(lock: Path) -> bool:
    deadline = time.monotonic() + STOP_TIMEOUT
    while _running(lock):
        if time.monotonic() > deadline:
            return False
        time.sleep(0.1)
    return True


def _accepts(listen: str) -> bool:
    """True if something accepts connections at `listen`."""
    host, _, port = listen.rpartition(":")
    host = {"": "127.0.0.1", "0.0.0.0": "127.0.0.1", "::": "::1"}.get(host.strip("[]"), host.strip("[]"))
    try:
        with socket.create_connection((host, int(port)), timeout=0.5):
            return True
    except OSError:
        return False


def _read(path: Path) -> dict | None:
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) and isinstance(record.get("id"), int) else None


def _forget(directory: Path, number: int | str) -> None:
    """Removes a proxy's description and lock, keeping its log."""
    for suffix in ("json", "lock"):
        (directory / f"{number}.{suffix}").unlink(missing_ok=True)
