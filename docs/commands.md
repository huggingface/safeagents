# Commands and options

| command | what it does |
| --- | --- |
| `safeagents start URL [PORT] [options]` | runs a proxy in the background and prints its ID |
| `safeagents ls` | lists the running proxies |
| `safeagents stop ID` | stops one |
| `safeagents URL [PORT] [options]` | runs a proxy in this terminal, with a live view of the calls |
| `safeagents inspect [N]` | lists the calls that were blocked or flagged, or shows call N in full |
| `safeagents scrub PATH... [--out DIR]` | reports the sensitive information in trajectories, and writes redacted copies to `DIR` |
| `safeagents publish PATH... --repo NAMESPACE/NAME` | redacts trajectories and uploads the redacted copies to a Hugging Face dataset |

`URL` is the model server your agent normally talks to. `PORT` is where the proxy listens (default `8787`, on `127.0.0.1`), or a full address such as `0.0.0.0:8080`.

| option | meaning |
| --- | --- |
| `--log-only` | report flagged calls without changing responses |
| `--allow-host HOST` | extra host calls may reach, subdomains included (repeatable) |
| `--no-scan` | request-only checks, no atif-scan |
| `--block-severity` | `low`, `medium`, `high` or `critical`: findings at or above it block without asking the judge |
| `--judge-url`, `--judge-model` | the decision model; its API key comes from `SAFEAGENTS_JUDGE_API_KEY` |
| `--judge-fail-closed` | block suspicious calls when the judge errors or gives no verdict (default: allow and log) |
| `--journal PATH` | where flagged calls are kept (default `~/.safeagents/calls.jsonl`) |
| `--no-journal` | keep no record of them |

The built-in host allowlist covers loopback, GitHub, PyPI, Hugging Face, npm and crates.io.

## Scrubbing and publishing trajectories

`scrub` and `publish` take JSON, JSON-lines (`.jsonl`, `.ndjson`) and text (`.txt`, `.md`, `.log`) files, or directories holding them. `scrub` exits with 1 when it found something, so it can gate a CI job.

| option | meaning |
| --- | --- |
| `--redact VALUE` | a value to mask everywhere: a name, a hostname, a project codename (repeatable) |
| `--no-env` | don't look for the values of this machine's secret-named environment variables |
| `--no-pii` | only mask credentials; keep emails, home directories and public IPs |
| `--out DIR` | (`scrub`) write redacted copies here |
| `--show` | (`scrub`) list each distinct redacted value once, with its kind, how often it was redacted, and one example in context; values are hinted (`sk-p…(32 chars)`) |
| `--show-all` | (`scrub`) list every redaction in context, file by file |
| `--reveal` | (`scrub --show`, `--show-all`) print the redacted values in full |
| `--path-in-repo DIR` | (`publish`) folder in the dataset |
| `--public` | (`publish`) create the dataset public if it doesn't exist (default: private) |
| `--dry-run` | (`publish`) redact and report, upload nothing |
| `-y`, `--yes` | (`publish`) upload without asking |
