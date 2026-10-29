# OpenCode

**Status: supported** through the `anthropic` provider (Anthropic Messages) or an `@ai-sdk/openai-compatible` provider (OpenAI Chat Completions).
Checked against OpenCode v1.19.3.

OpenCode reaches models through the Vercel AI SDK, and each provider in `opencode.json` takes an `options.baseURL`. Set it to safeagents with `/v1` at the end.

> [!WARNING]
> Don't proxy the built-in `openai` provider. Whatever `npm` package or `baseURL` you give it, OpenCode always calls the OpenAI **Responses API** (`/v1/responses`) for that provider, and safeagents doesn't guard that API. The same goes for the `xai` and `meta` providers, and for a ChatGPT Plus/Pro login. For OpenAI models, use a separate provider ID with `@ai-sdk/openai-compatible`, as shown below.

## 1. Start the proxy

```bash
# Claude models
uv run safeagents https://api.anthropic.com --log-only

# OpenAI models, or any OpenAI-compatible server (llama.cpp, vLLM, ...)
uv run safeagents https://api.openai.com --log-only
```

## 2. Point OpenCode at it

Edit `opencode.json` in your project, or `~/.config/opencode/opencode.json` to apply it everywhere.

**Anthropic Messages.** Override `baseURL` on the built-in `anthropic` provider:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "anthropic": { "options": { "baseURL": "http://127.0.0.1:8787/v1" } }
  },
  "model": "anthropic/claude-sonnet-4-5",
  "small_model": "anthropic/claude-haiku-4-5"
}
```

The key still comes from `ANTHROPIC_API_KEY` or `/connect`.

**OpenAI Chat Completions.** Declare your own provider ID with `@ai-sdk/openai-compatible`:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "safeagents-openai": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "OpenAI via safeagents",
      "options": {
        "baseURL": "http://127.0.0.1:8787/v1",
        "apiKey": "{env:OPENAI_API_KEY}"
      },
      "models": {
        "gpt-4.1": { "name": "GPT-4.1", "limit": { "context": 1047576, "output": 32768 } },
        "gpt-4.1-mini": { "name": "GPT-4.1 mini", "limit": { "context": 1047576, "output": 32768 } }
      }
    }
  },
  "disabled_providers": ["openai"],
  "model": "safeagents-openai/gpt-4.1",
  "small_model": "safeagents-openai/gpt-4.1-mini"
}
```

A custom provider ID has no entry in OpenCode's model catalog. List its models yourself under `models` and give each one a `limit`. `disabled_providers` keeps the unguarded `openai` provider out of the model picker.

**Keep `/v1` in `baseURL`.** The AI SDK appends `/messages` or `/chat/completions` to it, and safeagents forwards the path as is. Without `/v1`, the upstream receives `/messages` and answers 404.

## 3. Check it

Run `opencode`, choose the proxied model and ask it to list the files in the repo. The safeagents terminal should show the request and a `PASS` line for the call.

## Notes

- **Every model OpenCode uses needs to go through the proxy.** These settings can each name a model from a different provider, and that provider's traffic skips safeagents:
  - `small_model`, which writes session titles;
  - the `model` of an agent in `agent` config, which applies to subagents and the `compaction` and `title` agents.

  When they're unset, they fall back to the session's model or a small model from the same provider, which stays behind the proxy.
- OpenCode doesn't ask the proxy for a model list. Models come from `https://models.opencode.ai` and your config. `OPENCODE_DISABLE_MODELS_FETCH=1` uses the catalog bundled with OpenCode instead.
- On startup, OpenCode also runs `npm install @opencode-ai/plugin` in its config directory, which contacts the npm registry. This isn't model traffic and doesn't go through the proxy.
- With `anthropic`, OpenCode adds its own `anthropic-beta` headers. safeagents forwards them unchanged.

## Sources

- [OpenCode providers](https://opencode.ai/docs/providers/): `baseURL` overrides, and `@ai-sdk/openai-compatible` for `/v1/chat/completions`
- [OpenCode config](https://opencode.ai/docs/config/): `small_model`, `disabled_providers`
- [OpenCode agents](https://opencode.ai/docs/agents/): per-agent `model`
- [`provider.ts` at v1.19.3](https://github.com/anomalyco/opencode/blob/v1.19.3/packages/opencode/src/provider/provider.ts#L243-L264): `openai`, `xai` and `meta` forced onto `sdk.responses`
