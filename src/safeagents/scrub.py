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

- credentials: atif-scan's masking (token shapes, secret-named values, auth headers,
  URL passwords, OpenAI account ids). A value found once is masked everywhere it reappears.
- the secrets in this machine's environment (`OPENAI_API_KEY=...`), even printed bare.
- values listed with `--redact` (a name, a hostname, a project codename).
- emails, home directories (`/Users/alice`) and public IP addresses.

JSON is parsed and redacted string by string, so the output stays valid JSON. Redaction
is best-effort: review what `scrub` reports before publishing.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import shutil
import sys
import tempfile
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from atif_scan.data import account_ids, credentials
from atif_scan.evidence.cite import mask

from .constants import (
    EMAIL_PATTERN,
    GENERIC_HOME_USERS,
    HOME_PATTERN,
    IPV4_PATTERN,
    JSON_LINES_SUFFIXES,
    JSON_SUFFIXES,
    PREVIEW_CONTEXT,
    PUBLIC_EMAIL_DOMAINS,
    PUBLIC_EMAIL_USERS,
    REDACTED,
    TEXT_SUFFIXES,
)


@dataclass(frozen=True)
class Finding:
    """One redacted string: where it is, what was in it, and the string once redacted."""

    file: str
    where: str
    kinds: tuple[str, ...]
    preview: str

    def __str__(self) -> str:
        return f"{self.file}  {self.where}  {', '.join(self.kinds)}  {self.preview}"


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
        redacted = mask(text, known)
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

        def visit(value: object, where: str) -> object:
            if isinstance(value, str):
                redacted, kinds = self.redact_text(value, known)
                if kinds:
                    findings.append(Finding(file, where or "$", tuple(kinds), _preview(value, redacted)))
                return redacted
            if isinstance(value, dict):
                return {key: visit(item, f"{where}.{key}" if where else str(key)) for key, item in value.items()}
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
        return redacted, [Finding(f.file, "text", f.kinds, f.preview) for f in findings]

    def _scrub_lines(self, text: str, name: str) -> tuple[str, list[Finding]]:
        """JSON lines, one record at a time, with credentials masked across all of them."""
        records: list[object] = []
        for line in text.splitlines():
            try:
                records.append(json.loads(line))
            except ValueError:
                records.append(line)
        redacted, findings = self.scrub(records, name)
        if not findings:
            return text, []
        lines = []
        for original, record in zip(records, redacted, strict=True):
            if isinstance(original, str):
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
    _add_scrub_options(parser)
    args = parser.parse_args(argv)
    scrubber = _scrubber(args)
    found = _scrub_into(scrubber, trajectory_files(args.paths), args.out)
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
        leftover = _scrub_into(scrubber, trajectory_files(sorted(staged.iterdir())), None, quiet=True)
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
    scrubber: Scrubber, files: list[tuple[Path, str]], out: Path | None, quiet: bool = False
) -> list[Finding]:
    """Scrubs `files`, printing what was found and writing redacted copies under `out`."""
    found: list[Finding] = []
    for path, name in files:
        try:
            redacted, findings = scrubber.scrub_file(path, name)
        except UnicodeDecodeError:
            sys.exit(f"safeagents: {path} is not UTF-8 text")
        found += findings
        if not quiet:
            for finding in findings:
                print(finding)
        if out is not None:
            target = out / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if findings:
                target.write_text(redacted, encoding="utf-8")
            else:
                shutil.copyfile(path, target)
    if not quiet:
        print(f"{len(found)} strings to redact in {len(files)} files")
    return found


def _supported(path: Path) -> bool:
    return path.suffix.lower() in (*JSON_SUFFIXES, *JSON_LINES_SUFFIXES, *TEXT_SUFFIXES)


def _strings(value: object) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _known(texts: Iterable[str]) -> frozenset[str]:
    """Credential values anywhere in the trajectory, to mask them where they reappear bare."""
    texts = list(texts)
    return credentials.values(texts) | account_ids.values(texts)


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
    return Finding(finding.file, where, finding.kinds, finding.preview)
