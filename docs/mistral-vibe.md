# Mistral Vibe

**Status: supported** with the `mistral` backend (OpenAI Chat Completions through the Mistral SDK), or with a generic provider whose `api_style` is `openai` (Chat Completions) or `anthropic` (Anthropic Messages).
Checked against Mistral Vibe v3.0.4.

Vibe reads its providers and models from `config.toml`: `./.vibe/config.toml` first, then `~/.vibe/config.toml` (or `$VIBE_HOME/config.toml`). Each provider has an `api_base`. Set it to safeagents. Avoid `api_style = "openai-responses"`, which uses the OpenAI Responses API, and `"vertex-anthropic"`. safeagents doesn't guard those.

## 1. Start the proxy

```bash
# Mistral models
uv run safeagents https://api.mistral.ai --log-only

# Claude models
uv run safeagents https://api.anthropic.com --log-only

# OpenAI models, or any OpenAI-compatible server (llama.cpp, vLLM, ...)
uv run safeagents https://api.openai.com --log-only
```

## 2. Point Vibe at it

**Mistral models.** Override the built-in `mistral` provider in `~/.vibe/config.toml`. Keep `/v1` in `api_base`: Vibe strips it and the Mistral SDK adds `/v1/chat/completions` back.

```toml
[[providers]]
name = "mistral"
api_base = "http://127.0.0.1:8787/v1"
api_key_env_var = "MISTRAL_API_KEY"
backend = "mistral"
```

A provider entry replaces the built-in one with the same `name`, so set `backend = "mistral"` again. Without it, Vibe treats the provider as a generic OpenAI-compatible one. That still goes through the proxy, but it loses Mistral-specific handling. Your existing `active_model` and `[[models]]` keep working.

**OpenAI Chat Completions.** Declare a provider with `api_style = "openai"` and keep `/v1` in `api_base`. Vibe appends `/chat/completions`.

```toml
active_model = "gpt-4.1"

[utility_models]
title = "active"
smart_approve = "active"

[[providers]]
name = "openai"
api_base = "http://127.0.0.1:8787/v1"
api_key_env_var = "OPENAI_API_KEY"
api_style = "openai"

[[models]]
name = "gpt-4.1"
provider = "openai"
alias = "gpt-4.1"
```

**Anthropic Messages.** Use `api_style = "anthropic"` and leave `/v1` off. Vibe appends `/v1/messages` and sends the key as `x-api-key`.

```toml
active_model = "claude-sonnet"

[utility_models]
title = "active"
smart_approve = "active"

[[providers]]
name = "anthropic"
api_base = "http://127.0.0.1:8787"
api_key_env_var = "ANTHROPIC_API_KEY"
api_style = "anthropic"

[[models]]
name = "claude-sonnet-4-5"
provider = "anthropic"
alias = "claude-sonnet"
```

`active_model` takes a model's `alias`. Pin it: when it's unset, Vibe picks its default Mistral model, which is still on the unproxied `mistral` provider.

## 3. Check it

Run one prompt headless:

```bash
vibe --prompt "List the files in this directory" --auto-approve
```

The safeagents terminal should show the request and a `PASS` line for the call.

## Notes

- **Session titles and the smart-approve classifier can skip the proxy.** These utility completions use, in order: the model named in `[utility_models]`, then a fast Mistral model (`mistral-vibe-cli-fast` or `mistral-small-latest`) on the `mistral` provider, then the active model. With a non-Mistral active model and `MISTRAL_API_KEY` set, the fast model goes straight to `api.mistral.ai`. Set both `[utility_models]` entries to `"active"`, as above, or point the `mistral` provider at a proxy as well.
- **`compaction_model` and `vision_model` can name other models.** Vibe only uses a `compaction_model` on the active model's provider. A `vision_model`, which describes images for a model that can't see them, can be on any provider, and its calls skip safeagents unless that provider is proxied too.
- **Agent profiles can switch models.** A profile in `~/.vibe/agents/NAME.toml`, including a subagent's, can set its own `active_model`. That model's provider needs to go through the proxy too.
- **Expect a few extra requests through the proxy.** With the `mistral` backend, Vibe asks for `/v1/connectors/bootstrap` (disable with `enable_connectors = false`). It also asks for `/v1/models/<model>`, and on a fresh install it probes the fast utility models with one-token completions. safeagents forwards these unchanged. None of them carries a tool call.
- **These don't go through the proxy:** telemetry (`enable_telemetry = false` turns it off), OpenTelemetry traces (`enable_otel`), update checks, voice transcription and text to speech.
- Vibe also loads API keys from `~/.vibe/.env`. A variable already set in the environment wins.

## Sources

- [README: programmatic mode and configuration](https://github.com/mistralai/mistral-vibe/blob/v3.0.4/README.md#programmatic-mode)
- [`config/models.py`](https://github.com/mistralai/mistral-vibe/blob/v3.0.4/vibe/core/config/models.py): `ProviderConfig` (`api_base`, `api_style`, `backend`) and `[utility_models]`
- [`config/vibe_schema.py`](https://github.com/mistralai/mistral-vibe/blob/v3.0.4/vibe/core/config/vibe_schema.py): default providers, `compaction_model` and `vision_model` selection
- [`llm/utility_completion.py`](https://github.com/mistralai/mistral-vibe/blob/v3.0.4/vibe/core/llm/utility_completion.py): how titles and the classifier pick a model
- [`adapters/generic/_execute.py`](https://github.com/mistralai/mistral-vibe/blob/v3.0.4/harness/runtimes/python/python/mistralai_vibe_local_harness/vibe/adapters/generic/_execute.py): `api_style` adapters and endpoints
- [`adapters/mistral.py`](https://github.com/mistralai/mistral-vibe/blob/v3.0.4/harness/runtimes/python/python/mistralai_vibe_local_harness/vibe/adapters/mistral.py): the Mistral backend, via `client.chat.stream_async`
