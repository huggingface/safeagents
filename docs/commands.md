# Commands and options

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
