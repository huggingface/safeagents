# Codex CLI

**Status: not supported yet.** Codex only speaks the OpenAI Responses API, which safeagents doesn't handle.
Checked against Codex `rust-v0.163.0`.

Every Codex model request is `POST /v1/responses`. Codex removed Chat Completions support in v0.95.0 (February 2026), and `wire_api = "chat"` is now a config error:

```
`wire_api = "chat"` is no longer supported.
How to fix: set `wire_api = "responses"` in your provider config.
```

safeagents forwards Responses API traffic but can't read its tool calls. If you route Codex through it today, the calls go through unchecked and the monitor counts nothing. Support depends on safeagents learning the Responses API (listed under *Limits* in the README).

## Routing Codex through the proxy

This setup is ready for when Responses support lands. It's also useful for checking that traffic reaches the proxy.

```bash
uv run safeagents --upstream https://api.openai.com
```

Then in `~/.codex/config.toml` (or `$CODEX_HOME/config.toml`):

```toml
model = "<model-slug>"
model_provider = "safeagents"

[model_providers.safeagents]
name = "OpenAI via safeagents"
base_url = "http://127.0.0.1:8787/v1"
env_key = "OPENAI_API_KEY"
```

- **Keep `/v1` in `base_url`.** Codex appends `/responses` to it.
- **Use a custom provider, not `openai_base_url`.** Setting `openai_base_url` keeps the built-in `openai` provider, which tries WebSockets first (safeagents only proxies HTTP) and sends ChatGPT login tokens if you're signed in that way. A custom provider uses plain HTTP with server-sent events and your `OPENAI_API_KEY`.
- **Put this in your user config, not the project's.** A project-local `.codex/config.toml` can't set `model_provider`, `model_providers` or `openai_base_url`.
- **`OPENAI_BASE_URL` no longer works.** Codex stopped reading it for inference in April 2026.
- **Compaction stays on this provider.** On a custom provider, it runs as an ordinary `/responses` request, so it goes through the proxy too.

## Sources

- [Codex config reference](https://developers.openai.com/codex/config-reference): `model_providers`, `wire_api`
- [Advanced config](https://developers.openai.com/codex/config-advanced): `openai_base_url`, project config limits
- [`model-provider-info/src/lib.rs` at rust-v0.163.0](https://github.com/openai/codex/blob/rust-v0.163.0/codex-rs/model-provider-info/src/lib.rs#L100-L133): `WireApi::Responses` only
- [openai/codex#10157](https://github.com/openai/codex/pull/10157): Chat Completions removed; [discussion #7782](https://github.com/openai/codex/discussions/7782): deprecation notice
