# Claude Code

**Status: supported** (Anthropic Messages, JSON and streaming)
Checked against Claude Code v2.1.304.

Claude Code only talks to Claude models over the Anthropic Messages API, and it reads its endpoint from `ANTHROPIC_BASE_URL`. Point that variable at safeagents and every request goes through the proxy.

## 1. Start the proxy

Pick the upstream that Claude Code would normally use:

```bash
# Anthropic API (Console key or claude.ai subscription login)
uv run safeagents --upstream https://api.anthropic.com --log-only

# OpenRouter or another Anthropic-compatible gateway
uv run safeagents --upstream https://openrouter.ai/api --log-only
```

Don't add `/v1` to `--upstream`. Claude Code requests `/v1/messages` itself, and safeagents forwards that path as is.

## 2. Point Claude Code at it

**Terminal:**

```bash
ANTHROPIC_BASE_URL=http://127.0.0.1:8787 claude
```

Through OpenRouter, also pass the gateway key:

```bash
ANTHROPIC_BASE_URL=http://127.0.0.1:8787 ANTHROPIC_AUTH_TOKEN=$OPENROUTER_API_KEY claude
```

To make this permanent, put the variable in `~/.claude/settings.json`:

```json
{
  "env": { "ANTHROPIC_BASE_URL": "http://127.0.0.1:8787" }
}
```

**VS Code extension:** add this to your user settings JSON (*Preferences: Open User Settings (JSON)*), then run *Developer: Reload Window*:

```json
"claudeCode.environmentVariables": [
  { "name": "ANTHROPIC_BASE_URL", "value": "http://127.0.0.1:8787" }
]
```

## 3. Check it

Ask Claude Code to list the files in the current directory. The safeagents terminal should count one request and show a `PASS` line for the `Bash` or `Glob` call.

## Notes

- Keep the proxy running. Once `ANTHROPIC_BASE_URL` points at it, Claude Code can't reach the model without it. Remove the variable to go back.
- Headers are forwarded unchanged, `anthropic-beta` included, so API keys and subscription logins both keep working.
- `/v1/messages/count_tokens` requests also go through the proxy. They are forwarded but not counted as model turns.
- With a custom base URL, Claude Code turns off Remote Control, and tool search is off behind non-first-party gateways. The WebFetch domain check still goes straight to `api.anthropic.com`, not through the proxy.
- Claude Code declares every tool on every request, so the undeclared-tool check shouldn't fire. The host check will flag WebFetch calls to docs Claude knows without being told about them, such as `docs.rs`. Run with `--log-only` for a while, add the hosts you trust with `--allow-host`, then drop `--log-only`.
- Amazon Bedrock, Google Cloud Agent Platform and Microsoft Foundry use their own clients and endpoints. Those setups don't go through `ANTHROPIC_BASE_URL`, so this guide doesn't cover them.

## Sources

- [LLM gateway configuration](https://code.claude.com/docs/en/llm-gateway)
- [Environment variables](https://code.claude.com/docs/en/env-vars)
- [Settings files](https://code.claude.com/docs/en/settings)
