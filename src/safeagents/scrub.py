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
"""Finds and redacts sensitive information in agent trajectories before they are shared:
`safeagents scrub` and `safeagents publish`.

A trajectory is any JSON, JSON-lines or text file (ATIF traces, harness session logs,
request dumps). Every string in it is checked, wherever it sits, for:

- credentials: atif-scan's credential shapes and secret-named values, auth headers,
  URL passwords and OpenAI account ids. A value found once is masked everywhere it reappears.
- the secrets in this machine's environment (`OPENAI_API_KEY=...`), even printed bare.
- values listed with `--redact` (a name, a hostname, a project codename).
- emails, home directories (`/Users/alice`) and public IP addresses.

JSON is parsed and redacted string by string, so the output stays valid JSON. Redaction
is best-effort: review what `scrub` reports before publishing.
"""

from __future__ import annotations

import argparse
import dataclasses
import difflib
import ipaddress
import json
import os
import shutil
import sys
import tempfile
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from atif_scan.data import account_ids, credentials
from rich.console import Console
from rich.text import Text

from .constants import (
    AUTH_HEADER_PATTERN,
    BEARER_PATTERN,
    CHANGE_GAP,
    EMAIL_PATTERN,
    GENERIC_HOME_USERS,
    HINT_LENGTH,
    HOME_PATTERN,
    IPV4_PATTERN,
    JSON_LINES_SUFFIXES,
    JSON_SUFFIXES,
    KINDS,
    PII_MARKERS,
    PREVIEW_CONTEXT,
    PUBLIC_EMAIL_DOMAINS,
    PUBLIC_EMAIL_USERS,
    QUERY_SECRET_PATTERN,
    REDACTED,
    TEXT_SUFFIXES,
    URL_PASSWORD_PATTERN,
)


@dataclass(frozen=True)
class Finding:
    """One redacted string: where it is, what was in it, and the string once redacted.
    The string itself is kept out of `repr` and `str`, since it holds what was redacted."""

    file: str
    where: str
    kinds: tuple[str, ...]
    preview: str
    original: str = field(default="", repr=False, compare=False)
    redacted: str = field(default="", repr=False, compare=False)

    def __str__(self) -> str:
        return f"{self.file}  {self.where}  {', '.join(self.kinds)}  {self.preview}"


@dataclass(frozen=True)
class Change:
    """One span redaction replaced: `old` became `new`, at `start`:`end` of the redacted string."""

    old: str = field(repr=False)
    new: str
    start: int
    end: int


def changes(original: str, redacted: str) -> list[Change]:
    """What redaction replaced in `original`, compared line by line, then character by
    character within the changed lines."""
    old_lines = original.splitlines(keepends=True)
    new_lines = redacted.splitlines(keepends=True)
    line_starts = [0]
    for line in new_lines:
        line_starts.append(line_starts[-1] + len(line))
    found = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False).get_opcodes():
        if tag != "equal":
            found += _changed_spans("".join(old_lines[i1:i2]), "".join(new_lines[j1:j2]), line_starts[j1])
    return found


def environment_secrets(environ: Mapping[str, str] | None = None) -> frozenset[str]:
    """Values of the secret-named variables in `environ` (default: this process's)."""
    environ = os.environ if environ is None else environ
    return frozenset(
        value for name, value in environ.items() if credentials.secret_name(name) and credentials.plausible_value(value)
    )


class Scrubber:
    """Redacts trajectories. `listed` values are always masked; `environment` adds the
    secrets of this machine's environment; `pii` adds emails, home directories and IPs."""

    def __init__(self, listed: Iterable[str] = (), environment: bool = True, pii: bool = True) -> None:
        self.listed = frozenset(value for value in listed if value)
        self.environment = environment_secrets() if environment else frozenset()
        self.pii = pii

    def redact_text(self, text: str, known: frozenset[str] = frozenset()) -> tuple[str, list[str]]:
        """`text` redacted, and the kinds of what was found. `known` are credential values
        seen elsewhere in the same trajectory."""
        kinds = []
        for kind, values in (("listed value", self.listed), ("environment secret", self.environment)):
            redacted = _replace_values(text, values)
            if redacted != text:
                kinds.append(kind)
                text = redacted
        redacted = _mask_credentials(text, known)
        if redacted != text:
            kinds.append("credential")
            text = redacted
        if self.pii:
            for kind, redact in (("email", _emails), ("home directory", _homes), ("ip address", _ips)):
                redacted = redact(text)
                if redacted != text:
                    kinds.append(kind)
                    text = redacted
        return text, kinds

    def scrub(self, document: object, file: str = "") -> tuple[object, list[Finding]]:
        """A JSON value with every string redacted, and what was found. Credentials found
        in one string are masked wherever else they appear in `document`."""
        known = _known(_strings(document))
        findings: list[Finding] = []

        def redact(value: str, where: str) -> str:
            redacted, kinds = self.redact_text(value, known)
            if kinds:
                findings.append(Finding(file, where or "$", tuple(kinds), _preview(value, redacted), value, redacted))
            return redacted

        def visit(value: object, where: str) -> object:
            if isinstance(value, str):
                return redact(value, where)
            if isinstance(value, dict):
                # Keys too: Claude Code keys some records by file path, home directory included.
                redacted = {}
                for key, item in value.items():
                    key = redact(key, f"{where}.(key)" if where else "(key)")
                    redacted[key] = visit(item, f"{where}.{key}" if where else key)
                return redacted
            if isinstance(value, list):
                return [visit(item, f"{where}[{index}]") for index, item in enumerate(value)]
            return value

        return visit(document, ""), findings

    def scrub_file(self, path: Path, name: str | None = None) -> tuple[str, list[Finding]]:
        """The redacted content of a JSON, JSON-lines or text file, and what was found."""
        name = name or str(path)
        text = path.read_text(encoding="utf-8")
        suffix = path.suffix.lower()
        if suffix in JSON_SUFFIXES:
            try:
                document = json.loads(text)
            except ValueError:
                pass
            else:
                redacted, findings = self.scrub(document, name)
                if not findings:
                    return text, []
                return json.dumps(redacted, ensure_ascii=False, indent=2) + "\n", findings
        if suffix in JSON_LINES_SUFFIXES:
            return self._scrub_lines(text, name)
        redacted, findings = self.scrub(text, name)
        return redacted, [dataclasses.replace(finding, where="text") for finding in findings]

    def _scrub_lines(self, text: str, name: str) -> tuple[str, list[Finding]]:
        """JSON lines, one record at a time, with credentials masked across all of them."""
        originals = text.splitlines()
        records: list[object] = []
        for line in originals:
            try:
                records.append(json.loads(line))
            except ValueError:
                records.append(line)
        redacted, findings = self.scrub(records, name)
        if not findings:
            return text, []
        # Lines with nothing redacted are kept as they were, so a diff shows only the redactions.
        lines = []
        for line, original, record in zip(originals, records, redacted, strict=True):
            if record == original:
                lines.append(line)
            elif isinstance(original, str):
                lines.append(record)
            else:
                lines.append(json.dumps(record, ensure_ascii=False))
        findings = [_line_finding(finding) for finding in findings]
        return "\n".join(lines) + "\n", findings


def trajectory_files(paths: Sequence[Path]) -> list[tuple[Path, str]]:
    """Each file to scrub and its path relative to the argument it came from."""
    files = []
    for path in paths:
        if path.is_dir():
            for file in sorted(path.rglob("*")):
                relative = file.relative_to(path)
                hidden = any(part.startswith(".") for part in relative.parts)
                if file.is_file() and not hidden and _supported(file):
                    files.append((file, str(Path(path.name) / relative)))
        elif path.is_file():
            files.append((path, path.name))
        else:
            sys.exit(f"safeagents: no such file or directory: {path}")
    return files


def scrub(argv: list[str]) -> None:
    """Reports the sensitive information in trajectories, and optionally writes redacted copies."""
    parser = argparse.ArgumentParser(
        prog="safeagents scrub",
        description="Finds credentials, environment secrets and personal information in agent trajectories "
        "(JSON, JSON lines or text). Exits with 1 if anything was found.",
    )
    parser.add_argument("paths", nargs="+", type=Path, metavar="PATH", help="trajectory file or directory")
    parser.add_argument("--out", type=Path, metavar="DIR", help="write redacted copies here")
    parser.add_argument(
        "--show", action="store_true", help="show each distinct redacted value once, with a count and an example"
    )
    parser.add_argument("--show-all", action="store_true", help="show every redaction in context, file by file")
    parser.add_argument("--reveal", action="store_true", help="with --show or --show-all, print values in full")
    _add_scrub_options(parser)
    args = parser.parse_args(argv)
    scrubber = _scrubber(args)
    report = print
    if args.show_all:
        report = _shower(args.reveal)
    elif args.show:
        report = None
    found = _scrub_into(scrubber, trajectory_files(args.paths), args.out, report)
    if args.show and not args.show_all:
        _show_values(found, scrubber, args.reveal)
    if args.out:
        print(f"redacted copies in {args.out}")
    if found:
        sys.exit(1)


def publish(argv: list[str]) -> None:
    """Redacts trajectories and uploads the redacted copies to a Hugging Face dataset."""
    parser = argparse.ArgumentParser(
        prog="safeagents publish",
        description="Redacts agent trajectories like `safeagents scrub`, then uploads the redacted copies to a "
        "dataset on the Hugging Face Hub. The originals are never uploaded. Uses your `hf auth login` token.",
    )
    parser.add_argument("paths", nargs="+", type=Path, metavar="PATH", help="trajectory file or directory")
    parser.add_argument("--repo", required=True, metavar="NAMESPACE/NAME", help="dataset to upload to")
    parser.add_argument("--path-in-repo", default="", metavar="DIR", help="folder in the dataset (default: root)")
    parser.add_argument(
        "--public", action="store_true", help="create the dataset public if it doesn't exist (default: private)"
    )
    parser.add_argument("--dry-run", action="store_true", help="redact and report, upload nothing")
    parser.add_argument("-y", "--yes", action="store_true", help="upload without asking")
    _add_scrub_options(parser)
    args = parser.parse_args(argv)
    scrubber = _scrubber(args)
    files = trajectory_files(args.paths)
    if not files:
        sys.exit("safeagents: no trajectory files to publish")

    with tempfile.TemporaryDirectory(prefix="safeagents-publish-") as staging:
        staged = Path(staging)
        found = _scrub_into(scrubber, files, staged)
        # Redaction must leave nothing a second pass would find.
        leftover = _scrub_into(scrubber, trajectory_files(sorted(staged.iterdir())), None, report=None)
        if leftover:
            sys.exit(f"safeagents: {len(leftover)} strings still look sensitive after redaction; nothing uploaded")
        target = f"datasets/{args.repo}" + (f"/{args.path_in_repo}" if args.path_in_repo else "")
        print(f"{len(files)} files, {len(found)} strings redacted -> {target}")
        if args.dry_run:
            return
        if not args.yes and input("upload the redacted copies? [y/N] ").strip().lower() not in ("y", "yes"):
            sys.exit("safeagents: nothing uploaded")

        from huggingface_hub import HfApi

        api = HfApi()
        api.create_repo(args.repo, repo_type="dataset", private=not args.public, exist_ok=True)
        commit = api.upload_folder(
            repo_id=args.repo,
            repo_type="dataset",
            folder_path=staged,
            path_in_repo=args.path_in_repo or None,
            commit_message=f"Add {len(files)} trajectories (redacted with safeagents)",
        )
    print(f"uploaded: {commit.commit_url}")


def _add_scrub_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--redact", action="append", default=[], metavar="VALUE", help="a value to mask everywhere (repeatable)"
    )
    parser.add_argument(
        "--no-env", action="store_true", help="don't look for this machine's environment secrets in the files"
    )
    parser.add_argument("--no-pii", action="store_true", help="only credentials: keep emails, home paths and IPs")


def _scrubber(args: argparse.Namespace) -> Scrubber:
    return Scrubber(args.redact, environment=not args.no_env, pii=not args.no_pii)


def _scrub_into(
    scrubber: Scrubber,
    files: list[tuple[Path, str]],
    out: Path | None,
    report: Callable[[Finding], None] | None = print,
) -> list[Finding]:
    """Scrubs `files`, reporting what was found (unless `report` is None) and writing
    redacted copies under `out`."""
    found: list[Finding] = []
    for path, name in files:
        try:
            redacted, findings = scrubber.scrub_file(path, name)
        except UnicodeDecodeError:
            sys.exit(f"safeagents: {path} is not UTF-8 text")
        found += findings
        if report is not None:
            for finding in findings:
                report(finding)
        if out is not None:
            target = out / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if findings:
                target.write_text(redacted, encoding="utf-8")
            else:
                shutil.copyfile(path, target)
    if report is not None:
        print(f"{len(found)} strings to redact in {len(files)} files")
    return found


def _shower(reveal: bool) -> Callable[[Finding], None]:
    """Prints each redaction in context, under a header per file."""
    console = Console(highlight=False)
    shown_file = None

    def show(finding: Finding) -> None:
        nonlocal shown_file
        if finding.file != shown_file:
            console.print(Text(finding.file, style="bold underline"))
            shown_file = finding.file
        console.print(Text.assemble("  ", (finding.where, "bold"), "  ", (", ".join(finding.kinds), "cyan")))
        for change in changes(finding.original, finding.redacted):
            console.print(Text.assemble("    ", _in_context(finding.redacted, change, reveal)), soft_wrap=True)

    return show


def _show_values(findings: list[Finding], scrubber: Scrubber, reveal: bool) -> None:
    """Prints each distinct redacted value once: its kind, how often it was redacted, and
    where it was first seen, in context. Credentials first, then by count."""
    values: dict[tuple[str, str], list] = {}  # (old, new) -> [count, files, first finding, first change]
    for finding in findings:
        for change in changes(finding.original, finding.redacted):
            entry = values.setdefault((change.old, change.new), [0, set(), finding, change])
            entry[0] += 1
            entry[1].add(finding.file)
    rows = []
    for count, files, finding, change in values.values():
        kind = _kind(change, scrubber)
        rows.append((KINDS.index(kind), -count, kind, count, files, finding, change))
    rows.sort(key=lambda row: row[:2])

    console = Console(highlight=False)
    total_files = len({finding.file for finding in findings})
    total = sum(row[3] for row in rows)
    console.print(f"{len(rows)} distinct values, redacted {total} times in {total_files} files")
    for _, _, kind, count, files, finding, change in rows:
        old = change.old if reveal else _hint(change.old)
        spread = f"  in {len(files)} files" if len(files) > 1 else ""
        console.print(
            Text.assemble(
                f"{count:>6}× ",
                (f"{kind:<19}", "cyan"),
                (_one_line(old), "red"),
                " → ",
                (change.new, "green"),
                (spread, "dim"),
            ),
            soft_wrap=True,
        )
        console.print(
            Text.assemble(
                "        ", (f"{finding.file} {finding.where}: ", "dim"), _in_context(finding.redacted, change, reveal)
            ),
            soft_wrap=True,
        )


def _kind(change: Change, scrubber: Scrubber) -> str:
    """What a redacted value was, from what replaced it or the lists it came from."""
    if change.new in PII_MARKERS:
        return PII_MARKERS[change.new]
    if change.old in scrubber.listed:
        return "listed value"
    if change.old in scrubber.environment:
        return "environment secret"
    return "credential"


def _in_context(redacted: str, change: Change, reveal: bool) -> Text:
    """`before[old → new]after`, on one line, the old value hinted unless `reveal`."""
    begin = max(0, change.start - PREVIEW_CONTEXT)
    before = ("…" if begin else "") + redacted[begin : change.start]
    after = redacted[change.end : change.end + PREVIEW_CONTEXT]
    after += "…" if change.end + PREVIEW_CONTEXT < len(redacted) else ""
    old = change.old if reveal else _hint(change.old)
    return Text.assemble(
        (_one_line(before), "dim"),
        "[",
        (_one_line(old), "red"),
        " → ",
        (_one_line(change.new), "green"),
        "]",
        (_one_line(after), "dim"),
    )


def _hint(value: str) -> str:
    """The first few characters of a redacted value and its length, enough to recognise it."""
    shown = min(HINT_LENGTH, len(value) // 3)
    return f"{value[:shown]}…({len(value)} chars)"


def _one_line(text: str) -> str:
    return text.replace("\r", "").replace("\n", "⏎").replace("\t", " ")


def _changed_spans(old: str, new: str, offset: int) -> list[Change]:
    """Character-level changes from `old` to `new`, merging those separated by fewer than
    `CHANGE_GAP` equal characters: `alice@acme.io` -> `<email>` is one change, not several
    around the letters they share. Positions are offset by `offset`."""
    spans: list[list[int]] = []  # old start, old end, new start, new end
    for tag, a1, a2, b1, b2 in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        if spans and b1 - spans[-1][3] < CHANGE_GAP:
            spans[-1][1], spans[-1][3] = a2, b2
        else:
            spans.append([a1, a2, b1, b2])
    return [Change(old[a1:a2], new[b1:b2], offset + b1, offset + b2) for a1, a2, b1, b2 in spans]


def _supported(path: Path) -> bool:
    return path.suffix.lower() in (*JSON_SUFFIXES, *JSON_LINES_SUFFIXES, *TEXT_SUFFIXES)


def _strings(value: object) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _known(texts: Iterable[str]) -> frozenset[str]:
    """Credential values anywhere in the trajectory, to mask them where they reappear bare."""
    texts = list(texts)
    return credentials.values(texts) | account_ids.values(texts)


def _mask_credentials(text: str, known: frozenset[str]) -> str:
    """Credentials masked: atif-scan's credential shapes and secret-named values, OpenAI
    account ids, auth headers, URL passwords and secret query parameters, and any `known`
    value. Not atif-scan's citation masking, which also takes `max_tokens=12000` for a
    secret: fine for a short excerpt, not for a trajectory people will read."""
    text = _mask_spans(text, account_ids.spans(text))
    text = credentials.PRIVATE_KEY.sub("[private key]", text)
    text = AUTH_HEADER_PATTERN.sub(rf"\1: {REDACTED}", text)
    text = BEARER_PATTERN.sub(f"Bearer {REDACTED}", text)
    text = URL_PASSWORD_PATTERN.sub(rf"\1:{REDACTED}@", text)
    text = QUERY_SECRET_PATTERN.sub(rf"\1{REDACTED}", text)
    return credentials.mask(text, known)


def _mask_spans(text: str, spans: list[tuple[int, int]]) -> str:
    parts = []
    last = 0
    for start, end in sorted(spans):
        if start >= last:
            parts += [text[last:start], REDACTED]
            last = end
        else:
            last = max(last, end)
    parts.append(text[last:])
    return "".join(parts)


def _replace_values(text: str, values: frozenset[str]) -> str:
    for value in sorted(values, key=len, reverse=True):
        text = text.replace(value, REDACTED)
    return text


def _emails(text: str) -> str:
    def replace(match) -> str:
        user, domain = match["user"].lower(), match["domain"].lower()
        if user in PUBLIC_EMAIL_USERS or user.startswith(("noreply", "no-reply")) or domain in PUBLIC_EMAIL_DOMAINS:
            return match[0]
        return "<email>"

    return EMAIL_PATTERN.sub(replace, text)


def _homes(text: str) -> str:
    def replace(match) -> str:
        if match["user"].lower() in GENERIC_HOME_USERS:
            return match[0]
        return f"{match['root']}<user>"

    return HOME_PATTERN.sub(replace, text)


def _ips(text: str) -> str:
    def replace(match) -> str:
        try:
            public = ipaddress.ip_address(match[0]).is_global
        except ValueError:
            return match[0]
        return "<ip>" if public else match[0]

    return IPV4_PATTERN.sub(replace, text)


def _preview(original: str, redacted: str) -> str:
    """The redacted string around its first change, on one line."""
    start = next(
        (index for index, (a, b) in enumerate(zip(original, redacted, strict=False)) if a != b),
        min(len(original), len(redacted)),
    )
    begin = max(0, start - PREVIEW_CONTEXT)
    text = redacted[begin : start + 2 * PREVIEW_CONTEXT]
    text = " ".join(text.split())
    return ("…" if begin else "") + text + ("…" if start + 2 * PREVIEW_CONTEXT < len(redacted) else "")


def _line_finding(finding: Finding) -> Finding:
    """`[3].messages[0].content` in a JSON-lines file becomes `line 4: messages[0].content`."""
    index, _, rest = finding.where[1:].partition("]")
    where = f"line {int(index) + 1}" + (f": {rest.lstrip('.')}" if rest else "")
    return dataclasses.replace(finding, where=where)
