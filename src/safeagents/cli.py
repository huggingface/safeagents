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
"""safeagents <url> [port or host:port] [options]
safeagents inspect [N]"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

from atif_scan import Severity

from .guard import Config
from .journal import DEFAULT_PATH, Journal
from .policy import DEFAULT_ALLOWED_HOSTS

DESCRIPTION = """Proxies OpenAI- or Anthropic-compatible traffic to the upstream and blocks tool calls that
use an undeclared tool, reach a host appearing nowhere in the conversation, or trip
atif-scan's checks. Point your agent's base URL at the listen address. Run `safeagents inspect`
to see the calls it flagged."""


def main(argv: list[str] | None = None) -> None:
    if argv is None:
        argv = sys.argv[1:]
    if argv[:1] == ["inspect"]:
        inspect(argv[1:])
        return

    parser = argparse.ArgumentParser(prog="safeagents", description=DESCRIPTION)
    parser.add_argument("upstream", metavar="URL", help="model server base URL")
    parser.add_argument(
        "listen",
        nargs="?",
        default="127.0.0.1:8787",
        type=_listen,
        metavar="PORT_OR_ADDRESS",
        help="port or host:port to listen on; a bare port listens on 127.0.0.1 (default %(default)s)",
    )
    parser.add_argument(
        "--allow-host", action="append", default=[], metavar="HOST", help="host calls may reach (repeatable)"
    )
    parser.add_argument("--log-only", action="store_true", help="report flagged calls without changing responses")
    parser.add_argument("--no-scan", action="store_true", help="turn atif-scan's checks off")
    parser.add_argument(
        "--block-severity",
        default="high",
        choices=[severity.name.lower() for severity in Severity if severity > Severity.INFO],
        help="atif-scan findings at or above this are blocked outright; milder ones go to the judge (default %(default)s)",
    )
    parser.add_argument(
        "--journal",
        default=str(DEFAULT_PATH),
        metavar="PATH",
        help="file keeping every call that wasn't simply passed, with its full arguments (default %(default)s)",
    )
    parser.add_argument("--no-journal", action="store_true", help="keep no record of flagged calls")
    judge_group = parser.add_argument_group(
        "decision model", "Reviews calls with milder findings. Its API key is read from SAFEAGENTS_JUDGE_API_KEY."
    )
    judge_group.add_argument(
        "--judge-url", metavar="URL", help="OpenAI-compatible base URL, e.g. http://localhost:8080/v1"
    )
    judge_group.add_argument(
        "--judge-model", default="judge", help="model name sent to the judge (default %(default)s)"
    )
    judge_group.add_argument("--judge-timeout", type=float, default=30.0, metavar="SECONDS")
    judge_group.add_argument(
        "--judge-fail-closed", action="store_true", help="block suspicious calls when the judge fails"
    )
    args = parser.parse_args(argv)

    logging.basicConfig(format="%(message)s")
    logger = logging.getLogger("safeagents")
    logger.setLevel(logging.INFO)

    # Heavy imports after argument parsing, so --help stays fast.
    import uvicorn

    from .judge import Judge
    from .monitor import Monitor
    from .proxy import create_app
    from .scan import Scanner

    scanner = None
    if not args.no_scan:
        scanner = Scanner()
    judge = None
    if args.judge_url:
        api_key = os.environ.get("SAFEAGENTS_JUDGE_API_KEY")
        judge = Judge(args.judge_url, args.judge_model, api_key, args.judge_timeout)
    config = Config(
        block=not args.log_only,
        allowed_hosts=[*DEFAULT_ALLOWED_HOSTS, *args.allow_host],
        scanner=scanner,
        block_severity=Severity[args.block_severity.upper()],
        judge=judge,
        judge_fail_closed=args.judge_fail_closed,
        journal=None if args.no_journal else Journal(Path(args.journal).expanduser()),
    )

    host, _, port = args.listen.rpartition(":")
    setup_parts = ["block" if config.block else "log-only"]
    if config.scanner:
        setup_parts.append("atif-scan")
    if config.judge:
        setup_parts.append(f"judge {args.judge_url}")
    if config.journal:
        setup_parts.append(f"journal {config.journal.path}")
    setup = ", ".join(setup_parts)
    logger.info("[safeagents] listening on http://%s -> %s (%s)", args.listen, args.upstream, setup)
    with Monitor(setup=f"{args.listen} → {args.upstream}") as config.monitor:
        app = create_app(args.upstream, config)
        uvicorn.run(app, host=host or "127.0.0.1", port=int(port), log_level="warning")


def _listen(value: str) -> str:
    """A bare port listens on this machine only."""
    if value.isdigit():
        return f"127.0.0.1:{value}"
    return value


def inspect(argv: list[str]) -> None:
    """Lists the calls in the journal, or shows one of them in full."""
    from .journal import read
    from .monitor import summary

    parser = argparse.ArgumentParser(
        prog="safeagents inspect", description="Shows the tool calls safeagents kept: all of them, or one in full."
    )
    parser.add_argument("call", nargs="?", type=int, metavar="N", help="number of the call to show in full")
    parser.add_argument("--journal", default=str(DEFAULT_PATH), metavar="PATH", help="(default %(default)s)")
    args = parser.parse_args(argv)
    path = Path(args.journal).expanduser()
    entries = read(path)

    if args.call is None:
        if not entries:
            print(f"no calls recorded in {path}")
        for entry in entries:
            why = entry.get("reason") or ", ".join(signal["id"] for signal in entry.get("signals") or [])
            print(
                f"#{entry['id']:<5} {_when(entry)}  {entry.get('verdict', ''):<10}  "
                f"{entry.get('tool', ''):<12}  {summary(str(entry.get('arguments', '')))}  {why}".rstrip()
            )
        return

    entry = next((entry for entry in entries if entry["id"] == args.call), None)
    if entry is None:
        parser.exit(1, f"no call #{args.call} in {path}\n")
    rows = [
        ("verdict", entry.get("verdict")),
        ("tool", entry.get("tool")),
        ("model", entry.get("model")),
        ("task", entry.get("task")),
        ("reason", entry.get("reason")),
    ]
    for signal in entry.get("signals") or []:
        rows.append(("finding", f"{signal['id']} ({signal['severity']}): {signal['title']}"))
    rows.append(("judge", entry.get("judge")))
    print(f"call #{entry['id']}, {_when(entry)}")
    for name, value in rows:
        if value:
            print(f"{name:<8}  {value}")
    arguments = entry.get("arguments", "")
    try:
        arguments = json.dumps(json.loads(arguments), indent=2, ensure_ascii=False)
    except (TypeError, ValueError):
        pass
    print("arguments")
    print(arguments)


def _when(entry: dict) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(entry.get("time", 0)))


if __name__ == "__main__":
    main()
