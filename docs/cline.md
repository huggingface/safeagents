# Cline

**Status: supported** through the **Anthropic** provider with a custom base URL, or the **OpenAI Compatible** provider.
Checked against Cline v4.1.25.

The VS Code extension, the CLI and the SDK share one settings file, `~/.cline/data/settings/providers.json`. Point either of those two providers at safeagents. In both cases, **include `/v1` in the base URL**.

## 1. Start the proxy

```bash
# Claude models
uv run safeagents --upstream https://api.anthropic.com --log-only

# OpenAI models, or any OpenAI-compatible server
uv run safeagents --upstream https://api.openai.com --log-only
```

## 2. Point Cline at it

**VS Code:** open Cline's settings and choose one of:

- **API Provider: Anthropic.** Tick *Use custom base URL* and enter `http://127.0.0.1:8787/v1`. Ignore the field's placeholder (`https://api.anthropic.com`): without `/v1`, Cline requests `/messages` and the upstream answers 404.
- **API Provider: OpenAI Compatible.** Set *Base URL* to `http://127.0.0.1:8787/v1`, and fill in the API key and model ID.

**CLI:**

```bash
cline auth -p anthropic -k "$ANTHROPIC_API_KEY" -m claude-sonnet-4-6 -b http://127.0.0.1:8787/v1
cline auth -p openai-compatible -k "$OPENAI_API_KEY" -m gpt-4.1 -b http://127.0.0.1:8787/v1
```

Cline has no environment variable for the base URL. Use the settings panel or `cline auth`.

> [!WARNING]
> Don't pick the **OpenAI** provider (`openai-native`). It always uses the Responses API, which safeagents doesn't guard, and its VS Code panel has no base URL field anyway. Use **OpenAI Compatible**, which speaks Chat Completions.

## 3. Check it

Ask Cline, in Act mode, to list the files in the workspace. The safeagents terminal should show the request and a `PASS` line for the call.

## Notes

- **Set the proxy for both Plan and Act modes.** They keep separate model settings, and older versions also kept a separate provider per mode.
- **Compaction uses the active provider**, unless you've configured a separate summarizer. A summarizer with its own base URL must point at the proxy too.
- **Some providers ignore the base URL and bypass the proxy:** the default `cline` provider, the Claude Code provider (it runs the CLI as a subprocess) and OpenAI Codex (it talks to chatgpt.com).
- **OpenAI Compatible fetches the model list from `<base URL>/models`.** safeagents forwards that request unchanged.
- **Auth.** The Anthropic provider sends `x-api-key`. OpenAI Compatible sends `Authorization: Bearer` plus any custom headers you set.
- **Untested:** whether agents started with `spawn_agent` or teams inherit the parent's provider.

## Sources

- [Configuration](https://github.com/Cline/Cline/blob/v4.1.25/docs/getting-started/config.mdx): shared `providers.json`
- [CLI reference](https://github.com/Cline/Cline/blob/v4.1.25/docs/cli/cli-reference.mdx): `cline auth -p/-k/-m/-b`
- [OpenAI Compatible provider](https://github.com/Cline/Cline/blob/v4.1.25/docs/provider-config/openai-compatible.mdx)
- [`builtins.ts`](https://github.com/Cline/Cline/blob/v4.1.25/sdk/packages/llms/src/providers/builtins.ts): provider definitions and default base URLs
- [`builtins-runtime.ts`](https://github.com/Cline/Cline/blob/v4.1.25/sdk/packages/llms/src/providers/builtins-runtime.ts): `openai-native` uses Responses
