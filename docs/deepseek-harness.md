# DeepSeek Harness (dsh)

**Status: supported** with an API key, through the native DeepSeek adapter (Anthropic Messages) or the pi-ai adapter (Anthropic Messages or OpenAI Chat Completions).
Checked against dsh `dsh-v0.2.1-rc.2`.

By default, dsh talks to DeepSeek through its native `llm-deepseek` adapter, which speaks the Anthropic Messages protocol at `https://api.deepseek.com/anthropic`. Point that adapter's base URL at safeagents and make DeepSeek's endpoint the proxy's upstream.

## 1. Start the proxy

```bash
uv run safeagents https://api.deepseek.com/anthropic --log-only
```

## 2. Point dsh at it

**Option A: environment variable.** The simplest way. Put it in your shell, `<project>/.env` or `$DSH_HOME/.env` (`~/.dsh/.env` by default):

```bash
export DEEPSEEK_BASE_URL=http://127.0.0.1:8787
export DEEPSEEK_API_KEY=sk-...
dsh web
```

**Option B: profile patch.** Add this to `$DSH_HOME/profiles/<profile>/cordis.patch.yml`. The profile for `dsh web` is `web`. You can also keep it in a separate file and run `dsh web --patch ./safeagents.yml`:

```yaml
- id: llm-deepseek
  config:
    baseURL: http://127.0.0.1:8787
    apiKeyEnv: DEEPSEEK_API_KEY
```

A patch entry replaces that entry's whole `config`, so copy over any other fields you rely on.

The `/v1` suffix is optional. dsh adds it if it's missing, so `http://127.0.0.1:8787` and `http://127.0.0.1:8787/v1` both end up requesting `/v1/messages`. The URL must not contain credentials, a query string or a fragment.

The Python SDK sets the same variable for the dsh child process with `base_url="http://127.0.0.1:8787"`.

### Other providers through pi-ai

dsh also ships the `llm-pi-ai` adapter for Anthropic, OpenAI and other providers. Its routes are set in the same patch file, or in the web UI under *Models* → *Custom model API*:

```yaml
- id: llm-pi-ai
  config:
    providers:
      anthropic: { apiKeyEnv: ANTHROPIC_API_KEY, baseURL: http://127.0.0.1:8787 }
      openai:    { apiKeyEnv: OPENAI_API_KEY, api: openai-completions, baseURL: http://127.0.0.1:8787/v1 }
```

Start safeagents with the matching upstream (`https://api.anthropic.com` or `https://api.openai.com`). On this adapter, the URL is used exactly as you write it:

- **Anthropic:** leave `/v1` off, because the SDK appends `/v1/messages`.
- **OpenAI:** include `/v1`, and set `api: openai-completions`. The built-in `openai` route defaults to the Responses API, which safeagents doesn't guard.

## 3. Check it

Run one task without the web UI:

```bash
dsh headless "List the files in this directory"
```

The safeagents terminal should show the request and a `PASS` line for the call. The same works from a session in `dsh web`.

## Notes

- **Use an API key, not a DeepSeek account login.** The account route only sends its session token to `https://api.deepseek.com`. Moving it to another origin needs a grant from DeepSeek's platform.
- **Compaction, session titles and in-process subagents use the main route by default**, so they go through the proxy. If you set `summarizationProvider`/`summarizationModel`, or a title `provider`/`model`, that route must point at the proxy too.
- **Out-of-process subagent backends bypass the proxy.** These are the Codex, Claude Code and ACP backends, which run their own harnesses. Configure those harnesses separately (see [Claude Code](claude-code.md); [Codex](codex.md) isn't supported).
- **The `web_search` tool has its own endpoint** (`DEEPSEEK_SEARCH_BASE_URL`), so it doesn't go through the proxy. It runs server-side and returns no client tool calls, so there is nothing there for safeagents to check.
- dsh adds non-standard fields to its requests (`dsh_session_log`, `dsh_plugin_packages`, mid-conversation system messages). safeagents forwards them unchanged. It also includes them in the context it searches for hosts the conversation mentions.
- dsh requests are always streamed and refuse redirects. safeagents doesn't redirect, so this needs nothing from you.
- dsh sends telemetry to its own collector, by default only when you submit feedback. `DSH_TELEMETRY_DISABLED=1` turns it off.

## Sources

- [Providers guide](https://github.com/deepseek-ai/deepseek-harness/blob/dsh-v0.2.1-rc.2/docs/user/guide/providers.md): profiles, patch files, custom model API
- [`llm-deepseek` config](https://github.com/deepseek-ai/deepseek-harness/blob/dsh-v0.2.1-rc.2/packages/llm/llm-deepseek/src/config.ts#L115-L118): `baseURL`, then `DEEPSEEK_BASE_URL`, then the default
- [`messages-api.ts`](https://github.com/deepseek-ai/deepseek-harness/blob/dsh-v0.2.1-rc.2/packages/llm/llm-deepseek/src/messages-api.ts#L14-L17): `/v1` handling
- [`llm-pi-ai` provider](https://github.com/deepseek-ai/deepseek-harness/blob/dsh-v0.2.1-rc.2/packages/llm/llm-pi-ai/src/provider.ts#L47-L51): `api` values
- [DeepSeek account credentials](https://github.com/deepseek-ai/deepseek-harness/blob/dsh-v0.2.1-rc.2/packages/credentials/deepseek-account-platform/README.md): token bound to `inferenceOrigin`
