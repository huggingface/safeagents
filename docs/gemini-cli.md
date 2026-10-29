# Gemini CLI

**Status: not supported.** Gemini CLI only speaks the Gemini API, which safeagents doesn't handle.
Checked against Gemini CLI v0.64.0.

Every Gemini CLI model call uses Google's `@google/genai` client. It has no OpenAI- or Anthropic-format provider, even on the `gateway` auth type, which also speaks the Gemini API. Requests look like this:

- `POST /v1beta/models/{model}:streamGenerateContent?alt=sse` for the agent loop
- `POST /v1beta/models/{model}:generateContent`
- `POST /v1beta/models/{model}:countTokens`

safeagents forwards this traffic but can't read its tool calls (`functionCall` parts). Calls go through unchecked and the monitor counts nothing. Support depends on safeagents learning the Gemini `generateContent` format.

## Routing Gemini CLI through the proxy

This only works with a Gemini API key. Google-login (Code Assist) and Vertex AI traffic don't use this base URL.

```bash
uv run safeagents --upstream https://generativelanguage.googleapis.com
```

```bash
export GEMINI_API_KEY=...
export GOOGLE_GEMINI_BASE_URL=http://127.0.0.1:8787    # no /v1beta
gemini
```

Also pin the auth type in `~/.gemini/settings.json`:

```json
{ "security": { "auth": { "selectedType": "gemini-api-key" } } }
```

Without it, Gemini CLI sees `GOOGLE_GEMINI_BASE_URL` and picks the `gateway` auth type, which the normal CLI rejects with "Invalid auth method selected." Gateway is only wired up for ACP (`--acp`) and the SDK.

The API key goes upstream in the `x-goog-api-key` header, which safeagents forwards unchanged.

## Sources

- [Configuration reference at v0.64.0](https://github.com/google-gemini/gemini-cli/blob/v0.64.0/docs/reference/configuration.md): `GOOGLE_GEMINI_BASE_URL`
- [`contentGenerator.ts`](https://github.com/google-gemini/gemini-cli/blob/v0.64.0/packages/core/src/core/contentGenerator.ts#L63-L99): auth types and how they're picked from the environment
- [`auth.ts`](https://github.com/google-gemini/gemini-cli/blob/v0.64.0/packages/cli/src/config/auth.ts#L10-L49): auth types the CLI accepts
