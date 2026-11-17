# OpenCode

**Status: supported** on OpenCode 2.x and 1.x, through the `anthropic` provider (Anthropic Messages) or an OpenAI-compatible provider (OpenAI Chat Completions).
Checked against OpenCode v2.3.0 and v1.19.3.

Each provider in `opencode.json` takes a base URL: `settings.baseURL` in 2.x, `options.baseURL` in 1.x. Set it to safeagents with `/v1` at the end. Run `opencode --version` to see which major version you have: 2.x is the `@opencode/cli` npm package (or the `opencode.ai/v2/install` script), 1.x is `opencode-ai`.

> [!WARNING]
> Don't proxy the built-in `openai` provider. Whatever package or base URL you give it, OpenCode calls the OpenAI **Responses API** (`/v1/responses`) for that provider, in 2.x as in 1.x, and safeagents doesn't guard that API. In 1.x, the same goes for the `xai` and `meta` providers and for a ChatGPT Plus/Pro login; avoid them in 2.x too. For OpenAI models, use a separate provider ID with an OpenAI-compatible package, as shown below.

## 1. Start the proxy

```bash
# Claude models
uv run safeagents https://api.anthropic.com --log-only

# OpenAI models, or any OpenAI-compatible server (llama.cpp, vLLM, ...)
uv run safeagents https://api.openai.com --log-only
```

## 2. Point OpenCode at it

Edit `opencode.json` in your project, or `~/.config/opencode/opencode.json` to apply it everywhere.

**Keep `/v1` in the base URL.** OpenCode appends `/messages` or `/chat/completions` to it, and safeagents forwards the path as is. Without `/v1`, the upstream receives `/messages` and answers 404.

### OpenCode 2.x

**Anthropic Messages.** Override `settings.baseURL` on the built-in `anthropic` provider:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "providers": {
    "anthropic": { "settings": { "baseURL": "http://127.0.0.1:8787/v1" } }
  },
  "model": "anthropic/claude-sonnet-5-5",
  "agents": { "title": { "model": "anthropic/claude-haiku-5-5" } }
}
```

The key still comes from `ANTHROPIC_API_KEY` or `opencode auth`. Use a model that `opencode models` lists: 2.x replaces a model it doesn't know (`claude-sonnet-4-5`, for one) with its default one, without a warning.

**OpenAI Chat Completions.** Declare your own provider ID with `@opencode/ai/providers/openai-compatible`:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "providers": {
    "safeagents-openai": {
      "name": "OpenAI via safeagents",
      "package": "@opencode/ai/providers/openai-compatible",
      "env": ["OPENAI_API_KEY"],
      "settings": {
        "baseURL": "http://127.0.0.1:8787/v1",
        "apiKey": "{env:OPENAI_API_KEY}"
      },
      "models": {
        "gpt-4.1": { "name": "GPT-4.1", "limit": { "context": 1047576, "output": 32768 } },
        "gpt-4.1-mini": { "name": "GPT-4.1 mini", "limit": { "context": 1047576, "output": 32768 } }
      }
    }
  },
  "policies": [{ "action": "provider.use", "resource": "openai", "effect": "deny" }],
  "model": "safeagents-openai/gpt-4.1",
  "agents": { "title": { "model": "safeagents-openai/gpt-4.1-mini" } }
}
```

A custom provider ID has no entry in OpenCode's model catalog. List its models yourself under `models` and give each one a `limit`. The `policies` entry keeps the unguarded `openai` provider out of the model picker.

A 1.x config (below) still works in 2.x, but the keys above are the ones the 2.x docs describe.

### OpenCode 1.x

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

## 3. Check it

Run `opencode`, choose the proxied model and ask it to list the files in the repo. The safeagents terminal should show the request and a `PASS` line for the call.

## Notes

- **Every model OpenCode uses needs to go through the proxy.** These settings can each name a model from a different provider, and that provider's traffic skips safeagents:
  - `agents.title.model` (2.x) or `small_model` (1.x), which writes session titles;
  - the `model` of an agent in `agents` (2.x) or `agent` (1.x) config, which applies to subagents and the `compaction` and `title` agents.

  When they're unset, they fall back to the session's model or a small model from the same provider, which stays behind the proxy.
- **2.x runs sessions in a background service** shared by every OpenCode process of your user. Server settings, API keys from the environment among them, apply to that service when it starts. After changing one, run `opencode service restart`, or start OpenCode with `--standalone` for a private server.
- **2.x's `execute` tool** (Code Mode) runs code that calls the other tools, so a shell command can arrive inside an `execute` call instead of a `shell` one. safeagents reviews it the same way: its checks match the call's arguments, whichever tool carries them.
- OpenCode doesn't ask the proxy for a model list. Models come from OpenCode's catalog and your config. In 1.x, `OPENCODE_DISABLE_MODELS_FETCH=1` uses the catalog bundled with OpenCode instead of fetching `https://models.opencode.ai`.
- On startup, OpenCode 1.x also runs `npm install @opencode-ai/plugin` in its config directory, which contacts the npm registry. This isn't model traffic and doesn't go through the proxy.
- With `anthropic`, OpenCode adds its own `anthropic-beta` headers. safeagents forwards them unchanged.

## Sources

- [OpenCode 2 providers](https://opencode.ai/v2/docs/providers): `settings.baseURL` overrides and custom OpenAI-compatible providers
- [Migrating from OpenCode 1](https://opencode.ai/v2/docs/migrate-v1): renamed config keys (`provider` → `providers`, `options` → `settings`, `npm` → `package`, `small_model` → `agents.title.model`, ...)
- [OpenCode 2 CLI](https://opencode.ai/v2/docs/cli): the background service and `--standalone`
- [OpenCode 2 permissions](https://opencode.ai/v2/docs/permissions): `provider.use` policies
- [OpenCode providers](https://opencode.ai/docs/providers/): `baseURL` overrides, and `@ai-sdk/openai-compatible` for `/v1/chat/completions`
- [OpenCode config](https://opencode.ai/docs/config/): `small_model`, `disabled_providers`
- [OpenCode agents](https://opencode.ai/docs/agents/): per-agent `model`
- [`provider.ts` at v1.19.3](https://github.com/anomalyco/opencode/blob/v1.19.3/packages/opencode/src/provider/provider.ts#L243-L264): `openai`, `xai` and `meta` forced onto `sdk.responses`
