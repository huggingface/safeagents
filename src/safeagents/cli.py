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
    p = argparse.ArgumentParser(prog="safeagents", description=DESCRIPTION)
    p.add_argument("--upstream", required=True, help="model server base URL")
    p.add_argument("--listen", default="127.0.0.1:8787", help="address to listen on (default %(default)s)")
    p.add_argument("--allow-host", action="append", default=[], metavar="HOST", help="host calls may reach (repeatable)")
    p.add_argument("--log-only", action="store_true", help="report flagged calls without changing responses")
    p.add_argument("--no-scan", action="store_true", help="turn atif-scan's checks off")
    p.add_argument(
        "--block-severity",
        default="high",
        choices=[s.name.lower() for s in Severity if s > Severity.INFO],
        help="atif-scan findings at or above this are blocked outright; milder ones go to the judge (default %(default)s)",
    )
    judge = p.add_argument_group("decision model", "Reviews calls with milder findings. Its API key is read from SAFEAGENTS_JUDGE_API_KEY.")
    judge.add_argument("--judge-url", metavar="URL", help="OpenAI-compatible base URL, e.g. http://localhost:8080/v1")
    judge.add_argument("--judge-model", default="judge", help="model name sent to the judge (default %(default)s)")
    judge.add_argument("--judge-timeout", type=float, default=30.0, metavar="SECONDS")
    judge.add_argument("--judge-fail-closed", action="store_true", help="block suspicious calls when the judge fails")
    args = p.parse_args(argv)

    logging.basicConfig(format="%(message)s")
    logging.getLogger("safeagents").setLevel(logging.INFO)

    # Heavy imports after argument parsing, so --help stays fast.
    import uvicorn

    from .judge import Judge
    from .monitor import Monitor
    from .proxy import create_app
    from .scan import Scanner

    config = Config(
        block=not args.log_only,
        allowed_hosts=[*DEFAULT_ALLOWED_HOSTS, *args.allow_host],
        scanner=None if args.no_scan else Scanner(),
        block_severity=Severity[args.block_severity.upper()],
        judge=Judge(args.judge_url, args.judge_model, os.environ.get("SAFEAGENTS_JUDGE_API_KEY"), args.judge_timeout)
        if args.judge_url
        else None,
        judge_fail_closed=args.judge_fail_closed,
    )
    host, _, port = args.listen.rpartition(":")
    mode = "block" if config.block else "log-only"
    extras = [x for x in ("atif-scan" if config.scanner else "", f"judge {args.judge_url}" if config.judge else "") if x]
    setup = ", ".join([mode, *extras])
    logging.getLogger("safeagents").info("[safeagents] listening on http://%s -> %s (%s)", args.listen, args.upstream, setup)
    with Monitor(setup=f"{args.listen} → {args.upstream}") as config.monitor:
        uvicorn.run(create_app(args.upstream, config), host=host or "127.0.0.1", port=int(port), log_level="warning")

if __name__ == "__main__":
    main()
