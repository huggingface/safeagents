# OMP (oh-my-pi)

**Status: supported** for providers that use the `anthropic-messages` or `openai-completions` API.
Checked against OMP v19.1.1 (`@oh-my-pi/pi-coding-agent`, the `omp` CLI). OMP is a fork of [pi](pi.md) and needs [Bun](https://bun.sh) ≥ 1.4.4.

OMP reads providers from `~/.omp/agent/models.yml` (`models.yaml` also works). With a profile (`--profile NAME` or `OMP_PROFILE`), it's `~/.omp/profiles/NAME/agent/models.yml`. `PI_CODING_AGENT_DIR` replaces the default profile's agent directory. Set `baseUrl` on a provider to send its models through safeagents. There is no CLI flag for the base URL.

## 1. Start the proxy

```bash
# Claude models
uv run safeagents https://api.anthropic.com --log-only

# OpenRouter, or any OpenAI-compatible server
uv run safeagents https://openrouter.ai/api --log-only
```

## 2. Point OMP at it

Declare a provider with an explicit `api` and its own `models`. `apiKey` is the name of an environment variable holding the key, or a literal key.

**Anthropic.** OMP strips a trailing `/v1` and adds `/v1/messages`:

```yaml
providers:
  safe-anthropic:
    baseUrl: http://127.0.0.1:8787
    api: anthropic-messages
    apiKey: ANTHROPIC_API_KEY
    models:
      - id: claude-sonnet-4-5
```

**OpenRouter.** OMP's built-in `openrouter` provider uses the Responses API, which safeagents doesn't guard. Overriding its `api` doesn't change its built-in models, so declare a new provider with `api: openai-completions` and keep `/v1` in `baseUrl`:

```yaml
providers:
  safe-openrouter:
    baseUrl: http://127.0.0.1:8787/v1
    api: openai-completions
    apiKey: OPENROUTER_API_KEY
    models:
      - id: anthropic/claude-sonnet-4.5
```

```bash
omp --model safe-openrouter/anthropic/claude-sonnet-4.5
```

If you keep the built-in `openrouter` provider instead, set only its `baseUrl` (no `api`) and export `PI_OPENROUTER_RESPONSES=0` so it uses Chat Completions. See [openrouter.md](openrouter.md) for OpenRouter itself.

## 3. Check it

Start `omp` with the proxied model and ask it to list the files in the repo. The safeagents terminal should show the request and a `PASS` line for the call.

## Notes

- **Other models OMP uses** can bypass the proxy: the `smol`, `slow`, `plan`, `commit`, `task` (subagents), `advisor`, `vision` and `judge` roles, compaction, title generation and memory. Point every role you use at a proxied provider. `--offline` keeps judge, web search and image calls on the active provider.
- **Web search, MCP servers and the browser** reach the network directly, not through the model provider.
- **`.env` files** (`./.env`, `~/.omp/agent/.env`, `~/.omp/.env`, `~/.env`) can set `ANTHROPIC_BASE_URL` and `OPENAI_BASE_URL`. A `baseUrl` in `models.yml` wins for the provider it's on.
- At startup OMP probes local Ollama (11434), llama.cpp (8080) and LM Studio (1234) ports, and lists models for every built-in provider it has a key for. These aren't model calls.
- Self-protection blocks tool calls that set a base URL in `models.yml`, including profile copies.

## Sources

Read from the published package `@oh-my-pi/pi-coding-agent@19.1.1`:

- `src/sdk.ts`, `src/config/config-file.ts`: `<agentDir>/models.yml`, `models.json` migration
- `src/cli.ts`: profiles (`--profile`, `OMP_PROFILE`, `PI_PROFILE`)
- `src/config/models-config-schema-bundle.ts`: provider schema, `api` values
- `src/config/model-patch.ts`: provider-level `api` doesn't retarget built-in models
- `dist/cli.js`: `openrouter` api routes to Responses unless `PI_OPENROUTER_RESPONSES=0`
