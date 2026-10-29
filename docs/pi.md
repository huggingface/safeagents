# pi

**Status: supported** for providers that use the `anthropic-messages` or `openai-completions` API.
Checked against pi v1.3.0.

pi reads endpoint overrides from `~/.pi/agent/models.json` (or `$PI_CODING_AGENT_DIR/models.json`). Setting `baseUrl` on a provider sends all of that provider's models through safeagents. pi ignores `ANTHROPIC_BASE_URL` and `OPENAI_BASE_URL`, and has no CLI flag for the base URL.

## 1. Start the proxy

```bash
# Claude models
uv run safeagents --upstream https://api.anthropic.com --log-only

# OpenAI models
uv run safeagents --upstream https://api.openai.com --log-only
```

## 2. Point pi at it

**Anthropic.** Override the built-in provider, without `/v1` (the SDK adds `/v1/messages`):

```json
{
  "providers": {
    "anthropic": { "baseUrl": "http://127.0.0.1:8787" }
  }
}
```

**OpenAI, or any OpenAI-compatible server.** pi's built-in `openai` provider uses the Responses API, which safeagents doesn't guard. Overriding the provider's `api` doesn't change its built-in models, so declare a new provider with `api: "openai-completions"` and keep `/v1` in `baseUrl`:

```json
{
  "providers": {
    "safe-openai": {
      "baseUrl": "http://127.0.0.1:8787/v1",
      "api": "openai-completions",
      "apiKey": "$OPENAI_API_KEY",
      "models": [{ "id": "gpt-5.4" }]
    }
  }
}
```

```bash
pi --model safe-openai/gpt-5.4
```

**DeepSeek's built-in provider** speaks Chat Completions with base URL `https://api.deepseek.com`. Run the proxy with `--upstream https://api.deepseek.com` and set:

```json
{ "providers": { "deepseek": { "baseUrl": "http://127.0.0.1:8787" } } }
```

`models.json` accepts comments. `apiKey` can be a literal key, `$ENV`/`${ENV}`, or `!command`.

## 3. Check it

Start `pi` with the proxied model and ask it to list the files in the repo. The safeagents terminal should show the request and a `PASS` line for the call.

## Which providers can be proxied

| API | Providers | Through safeagents |
| --- | --- | --- |
| `anthropic-messages` | Anthropic, MiniMax, Vercel AI Gateway | yes |
| `openai-completions` | DeepSeek, Groq, Hugging Face, OpenRouter and most others | yes |
| `openai-responses` and variants | OpenAI, xAI, Meta, Azure (except Foundry models), OpenAI Codex (ChatGPT login) | no: use a custom `openai-completions` provider |
| Gemini, Vertex, Bedrock, Mistral | Google, Vertex, Bedrock, Mistral | no |
| mixed, per model | GitHub Copilot | depends on the model's API |

## Notes

- Compaction and branch summaries reuse the session model, so they go through the proxy.
- pi has no built-in subagents. The example `subagent` extension starts `pi` subprocesses, which read the same `models.json`.
- **These bypass the proxy:**
  - classifier and image models reached through `codemode`;
  - virtual-model routing;
  - providers registered by extensions.
- pi also contacts pi.dev for its catalog, version checks and install reports. These aren't model calls. `PI_OFFLINE` and `PI_SKIP_VERSION_CHECK` turn some of them off.
- Anthropic requests go to `/v1/messages?beta=true`. safeagents forwards the query string and guards them like any other Messages request.

## Sources

- [Configuration](https://github.com/earendil-works/pi/blob/v1.3.0/packages/coding-agent/docs/configuration.md): `models.json` location
- [Models](https://github.com/earendil-works/pi/blob/v1.3.0/packages/coding-agent/docs/models.md): custom providers, `apiKey` formats
- [`model-config.ts`](https://github.com/earendil-works/pi/blob/v1.3.0/packages/coding-agent/src/core/model-config.ts#L32-L78): provider schema
- [`types.ts`](https://github.com/earendil-works/pi/blob/v1.3.0/packages/ai/src/types.ts#L61-L71): `api` values
- [`openai.ts`](https://github.com/earendil-works/pi/blob/v1.3.0/packages/ai/src/providers/openai.ts#L9-L14): built-in `openai` uses Responses
