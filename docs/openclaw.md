# OpenClaw

**Status: supported** through custom providers with `api: "anthropic-messages"` or `api: "openai-completions"`.
Checked against OpenClaw v2026.10.5.

OpenClaw reads its config from `~/.openclaw/openclaw.json`, a JSON5 file; `OPENCLAW_CONFIG_PATH` overrides the location. Each provider under `models.providers` takes a `baseUrl` and an `api` that picks the wire protocol. Declare providers that point at safeagents and make one of their models the default.

## 1. Start the proxy

```bash
# Claude models
uv run safeagents --upstream https://api.anthropic.com --log-only

# OpenAI models, or any OpenAI-compatible server
uv run safeagents --upstream https://api.openai.com --log-only
```

## 2. Point OpenClaw at it

```json5
{
  agents: {
    defaults: {
      model: { primary: "safeagents-anthropic/claude-sonnet-4-6" },
    },
  },
  models: {
    mode: "merge",
    providers: {
      "safeagents-anthropic": {
        baseUrl: "http://127.0.0.1:8787",
        apiKey: "${ANTHROPIC_API_KEY}",
        api: "anthropic-messages",
        models: [{ id: "claude-sonnet-4-6", name: "Sonnet", input: ["text", "image"] }],
      },
      "safeagents-openai": {
        baseUrl: "http://127.0.0.1:8787/v1",
        apiKey: "${OPENAI_API_KEY}",
        api: "openai-completions",
        models: [{ id: "gpt-4.1", name: "GPT-4.1" }],
      },
    },
  },
}
```

You can make the same change from the CLI:

```bash
openclaw config set models.providers.safeagents-openai '<provider json>' --strict-json --merge
openclaw models set safeagents-openai/gpt-4.1
```

- **`anthropic-messages`:** `/v1` is optional, and OpenClaw's docs leave it off. The client requests `/v1/messages` either way.
- **`openai-completions`:** include `/v1`, because the client appends `/chat/completions`.
- **Don't proxy the bundled `openai` provider.** It uses `api: "openai-responses"`, which safeagents doesn't guard. A custom provider with a `baseUrl` and no `api` defaults to `openai-completions`.
- OpenClaw trusts a loopback `baseUrl` automatically, so you need no extra network setting.

## 3. Check it

Start a session on the proxied model and ask it to list the files in the workspace. The safeagents terminal should show the request and a `PASS` line for the call.

## Notes

- **Several models can be set apart from the primary one.** Any of these that names an unproxied provider bypasses safeagents:
  - `agents.defaults.compaction.model` and `compaction.memoryFlush.model`;
  - `heartbeat.model`;
  - `utilityModel`, used for titles, recaps and narration. It is derived automatically, as `gpt-5.6-luna` for OpenAI or `claude-haiku-4-5` for Anthropic, on the *bundled* provider;
  - `imageModel`, `pdfModel`, `mediaModels.*` and `fallbacks`;
  - `agents.defaults.subagents.model`, which inherits the caller's model when unset.

  Point each one you use at a `safeagents-*` provider.
- **The Codex app-server runtime and the `claude-cli` runtime bypass the proxy.** They don't use the configured base URL.
- **On a non-Anthropic URL, OpenClaw drops its implicit `anthropic-beta` headers.** If you need beta features, set `headers: { "anthropic-beta": "..." }` on the provider.
- **Auth headers.** OpenClaw sends `x-api-key`, or `Authorization: Bearer` for OAuth tokens or when `authHeader: true` is set. safeagents forwards both.
- **GPT-5.x reasoning models with tools may not work this way.** OpenClaw keeps Chat Completions for non-OpenAI hosts such as the proxy. OpenAI may answer 400 for those models, because it wants the Responses API for them. This is untested. If it happens, use a model that works on Chat Completions.

## Sources

- [Configuration](https://github.com/openclaw/openclaw/blob/v2026.10.5/docs/gateway/configuration.md) and [custom providers](https://github.com/openclaw/openclaw/blob/v2026.10.5/docs/gateway/config-tools/custom-providers.md)
- [Model provider quick rules](https://github.com/openclaw/openclaw/blob/v2026.10.5/docs/concepts/model-providers/quick-rules.md): runtimes, `openclaw models set`
- [Agent model settings](https://github.com/openclaw/openclaw/blob/v2026.10.5/docs/gateway/config-agents/models.md): `utilityModel`, `imageModel`, fallbacks
- [`anthropic-transport-stream.ts`](https://github.com/openclaw/openclaw/blob/v2026.10.5/packages/ai/src/transports/anthropic-transport-stream.ts#L76-L87): `/v1` handling
- [`extensions/openai/openclaw.plugin.json`](https://github.com/openclaw/openclaw/blob/v2026.10.5/extensions/openai/openclaw.plugin.json#L52-L53): bundled `openai` uses Responses
