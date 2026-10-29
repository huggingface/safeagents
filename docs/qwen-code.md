# Qwen Code

**Status: supported** with the `openai` auth type (OpenAI Chat Completions). The `anthropic` auth type works only with gateways that accept bearer-token auth; see below.
Checked against Qwen Code v0.25.3.

Qwen Code picks a wire protocol from its auth type. Use `openai` with Chat Completions and set the base URL to safeagents with `/v1`. Avoid `openai-responses` and `"wireApi": "responses"`: those use the OpenAI Responses API, which safeagents doesn't guard.

## 1. Start the proxy

Use the origin of your model server as the upstream:

```bash
# OpenAI
uv run safeagents --upstream https://api.openai.com --log-only

# Alibaba Cloud DashScope (Qwen models)
uv run safeagents --upstream https://dashscope.aliyuncs.com/compatible-mode --log-only
```

## 2. Point Qwen Code at it

**`~/.qwen/settings.json`:**

```json
{
  "modelProviders": {
    "openai": [
      {
        "id": "qwen3-coder-plus",
        "baseUrl": "http://127.0.0.1:8787/v1",
        "envKey": "OPENAI_API_KEY",
        "wireApi": "chat-completions"
      }
    ]
  },
  "security": { "auth": { "selectedType": "openai" } },
  "model": { "name": "qwen3-coder-plus" }
}
```

`model.name` must match an `id` in the list. Without `wireApi`, Qwen Code defaults to Chat Completions. Setting it explicitly also stops Qwen Code from switching to a Responses route at startup.

**Or with environment variables.** Set all three; Qwen Code only infers the auth type when the whole set is present:

```bash
export OPENAI_API_KEY=sk-...
export OPENAI_BASE_URL=http://127.0.0.1:8787/v1
export OPENAI_MODEL=qwen3-coder-plus
qwen
```

**Or with flags:** `qwen --auth-type openai --openai-base-url http://127.0.0.1:8787/v1 --openai-api-key ... --model qwen3-coder-plus`. An entry in `modelProviders` takes priority over the flags, and the flags over environment variables.

Keep `/v1` in the base URL. The OpenAI SDK appends `/chat/completions`. In the `/auth` UI, choose *Custom Provider* → *OpenAI* and enter the same URL.

### DashScope (Qwen models)

Qwen Code recognises DashScope by its hostname. With the proxy at `127.0.0.1`, it loses its DashScope-specific behaviour: prompt caching headers and `cache_control` parts. To keep it, set this variable to the exact base URL:

```bash
export DASHSCOPE_PROXY_BASE_URL=http://127.0.0.1:8787/v1
```

The DeepSeek, Z.ai (`glm-*`), MiMo and Mistral adapters also match on the model name, so they keep working behind the proxy. The ModelScope, MiniMax, Cerebras and Fireworks adapters only match on the hostname, so they don't.

## 3. Check it

Run one prompt headless (`-p` is deprecated; pass the prompt directly):

```bash
qwen --approval-mode yolo "List the files in this directory"
```

The safeagents terminal should show the request and a `PASS` line for the call.

## Anthropic

```json
{
  "modelProviders": {
    "anthropic": [
      { "id": "claude-sonnet-4-6", "baseUrl": "http://127.0.0.1:8787", "envKey": "ANTHROPIC_API_KEY" }
    ]
  },
  "security": { "auth": { "selectedType": "anthropic" } },
  "model": { "name": "claude-sonnet-4-6" }
}
```

- **Leave `/v1` off.** The SDK adds `/v1/messages`. The docs and the `/auth` UI suggest `https://api.anthropic.com/v1`, which would request `/v1/v1/messages`.
- **Auth is the problem.** For any host other than `*.anthropic.com`, Qwen Code sends the key as `Authorization: Bearer`, not `x-api-key`. It also sends a `claude-cli` user agent. safeagents forwards these unchanged. Gateways that accept bearer tokens (for example `--upstream https://openrouter.ai/api`) should work. `api.anthropic.com` probably rejects an API key sent that way. This is untested. For Claude models, prefer an OpenAI-compatible gateway or another harness.

## Notes

- **Every model Qwen Code uses must go through the proxy over Chat Completions or Messages.** These can each name a model from another provider or protocol, and that model's calls then skip safeagents:
  - `fastModel`, used by the auto-mode classifier, web-fetch summaries, session recaps, tool-use summaries and follow-up suggestions;
  - `compactionModel`, `advisorModel`, `visionModel` and `imageModel`;
  - `agents.builtin.exploreModel`;
  - a subagent's `model`.

  When unset, most of these fall back to the main model.
- **Auto-memory sends an extra model call after each turn.** `memory.enableManagedAutoMemory` (on by default) resends the conversation to the main model with its own tools. It goes through the proxy and gets checked like the main loop, so a flagged call can show up as a second `BLOCK` line.
- **Expect a `respond_in_schema` tool call on classifier and other JSON requests.** These side queries force that tool call, and the proxy checks it like any other.
- **These bypass the proxy:**
  - `web_search`, which runs on DashScope's Responses API and only on recognised DashScope hosts;
  - the built-in `claude-code` and `codex` subagents, which run their own executables.
- **Model discovery in `/auth` asks the proxy for `{baseUrl}/models`.** safeagents forwards that request unchanged.
- **`.env` files load from the first one found:** `.qwen/.env`, then `.env` walking up from the current directory, then `~/.qwen/.env`, then `~/.env`. Only that one file is read.

## Sources

- [Authentication](https://github.com/QwenLM/qwen-code/blob/v0.25.3/docs/users/configuration/auth.md): `modelProviders`, environment variables, `.env` lookup
- [Model providers](https://github.com/QwenLM/qwen-code/blob/v0.25.3/docs/users/configuration/model-providers.md): `wireApi`, CLI flags, precedence
- [Settings](https://github.com/QwenLM/qwen-code/blob/v0.25.3/docs/users/configuration/settings.md): `fastModel`, `compactionModel` and other side models
- [`openaiContentGenerator/index.ts`](https://github.com/QwenLM/qwen-code/blob/v0.25.3/packages/core/src/core/openaiContentGenerator/index.ts#L62-L133): vendor adapter selection
- [`anthropicContentGenerator.ts`](https://github.com/QwenLM/qwen-code/blob/v0.25.3/packages/core/src/core/anthropicContentGenerator/anthropicContentGenerator.ts#L184-L196): auth headers for non-Anthropic hosts
