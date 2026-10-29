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
"""safeagents --upstream <url> [options]"""

from __future__ import annotations

import argparse
import logging
import os

from atif_scan import Severity

from .guard import Config
from .policy import DEFAULT_ALLOWED_HOSTS

DESCRIPTION = """Proxies OpenAI- or Anthropic-compatible traffic to <url> and blocks tool calls that
use an undeclared tool, reach a host appearing nowhere in the conversation, or trip
atif-scan's checks. Point your agent's base URL at --listen."""


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="safeagents", description=DESCRIPTION)
    parser.add_argument("--upstream", required=True, help="model server base URL")
    parser.add_argument("--listen", default="127.0.0.1:8787", help="address to listen on (default %(default)s)")
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
    )

    host, _, port = args.listen.rpartition(":")
    setup_parts = ["block" if config.block else "log-only"]
    if config.scanner:
        setup_parts.append("atif-scan")
    if config.judge:
        setup_parts.append(f"judge {args.judge_url}")
    setup = ", ".join(setup_parts)
    logger.info("[safeagents] listening on http://%s -> %s (%s)", args.listen, args.upstream, setup)
    with Monitor(setup=f"{args.listen} → {args.upstream}") as config.monitor:
        app = create_app(args.upstream, config)
        uvicorn.run(app, host=host or "127.0.0.1", port=int(port), log_level="warning")


if __name__ == "__main__":
    main()
