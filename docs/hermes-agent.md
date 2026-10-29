# Hermes Agent

**Status: supported** through the `anthropic` provider or named providers with `transport: anthropic_messages` or `transport: chat_completions`.
Checked against Hermes Agent v0.22.2.

Hermes reads its config from `~/.hermes/config.yaml` and secrets from `~/.hermes/.env`. Point the main provider at safeagents. Then pin the auxiliary tasks to that provider too, because they can otherwise fall back to other providers you have keys for.

## 1. Start the proxy

```bash
# Claude models
uv run safeagents https://api.anthropic.com --log-only

# OpenAI models, or any OpenAI-compatible server
uv run safeagents https://api.openai.com --log-only
```

## 2. Point Hermes at it

**Anthropic, the short way.** Put the base URL in `~/.hermes/.env`:

```bash
ANTHROPIC_BASE_URL=http://127.0.0.1:8787
ANTHROPIC_API_KEY=sk-ant-...
```

and select the provider in `~/.hermes/config.yaml`:

```yaml
model:
  provider: anthropic
  default: claude-sonnet-4-6
```

**Named providers.** Use these for OpenAI-compatible models, or to keep both protocols side by side:

```yaml
providers:
  safeagents-anthropic:
    api: "http://127.0.0.1:8787"
    key_env: ANTHROPIC_API_KEY
    transport: anthropic_messages
  safeagents-openai:
    api: "http://127.0.0.1:8787/v1"
    key_env: OPENAI_API_KEY
    transport: chat_completions

model:
  provider: safeagents-openai
  default: gpt-4.1
```

`hermes model` also has a *Custom Endpoint* wizard that asks for the API mode. Pick `chat_completions`.

- **Anthropic:** `/v1` is optional. Hermes strips it and the SDK adds `/v1/messages`.
- **Chat Completions:** include `/v1`.
- **Don't proxy the built-in `openai-api` provider through `OPENAI_BASE_URL`.** That provider uses the Responses API, which safeagents doesn't guard, and it keeps doing so with a custom URL. Use a named provider with `transport: chat_completions`.

## 3. Pin the auxiliary tasks

Compression, titles, vision, session search and background review each pick a provider through `auxiliary.<task>.provider`. The default, `auto`, tries the main provider first, but not always:

- **Vision** jumps to OpenRouter, Nous, Anthropic or a custom endpoint when the main model can't read images.
- **On failure**, if the main route fails and no fallback policy is set, Hermes tries the other providers it has credentials for.

To keep these tasks behind the proxy, set them explicitly:

```yaml
auxiliary:
  compression: { provider: safeagents-openai }
  title:       { provider: safeagents-openai }
  vision:      { provider: safeagents-openai }
```

Do the same for `delegation.provider`, which subagents inherit from the main model when it's unset. Also check `fallback_providers` and any cron job that sets its own provider.

## 4. Check it

Run `hermes chat` and ask it to list the files in the current directory. The safeagents terminal should show the request and a `PASS` line for the call.

## Notes

- **Use an API key for Claude.** A base URL without `anthropic.com` makes Hermes treat the endpoint as third-party: it sends `x-api-key` and skips OAuth. A Claude Max login through the proxy will likely fail.
- **Hermes lists models from `/v1/models` through the proxy.** safeagents forwards those requests unchanged.
- **Chat Completions requests carry `reasoning_effort: medium`** unless the model is marked non-reasoning.
- **Extended thinking with tools through the proxy is untested.** Hermes changes how it handles thinking signatures for third-party base URLs.
- **GPT-5.x reasoning models with tools may be rejected.** Hermes keeps Chat Completions for the proxy URL. OpenAI may answer 400 for those models, because it wants the Responses API for them. This is untested.

## Sources

- [Providers](https://github.com/NousResearch/hermes-agent/blob/v0.22.2/website/docs/integrations/providers.md): named providers, custom endpoints, `api_mode`
- [Configuring models](https://github.com/NousResearch/hermes-agent/blob/v0.22.2/website/docs/user-guide/configuring-models.md): `auxiliary.*`, `extra_headers`
- [Environment variables](https://github.com/NousResearch/hermes-agent/blob/v0.22.2/website/docs/reference/environment-variables.md): `ANTHROPIC_BASE_URL`
- [`hermes_cli/providers.py`](https://github.com/NousResearch/hermes-agent/blob/v0.22.2/hermes_cli/providers.py#L34-L35): `openai-api` uses `codex_responses`
- [`agent/auxiliary_client.py`](https://github.com/NousResearch/hermes-agent/blob/v0.22.2/agent/auxiliary_client.py): auxiliary provider resolution and fallbacks
