# OpenRouter

OpenRouter is a model server, not a harness: any harness on this list that can talk to an OpenAI-compatible server can reach OpenRouter through safeagents. safeagents guards OpenRouter's **Chat Completions** endpoint (`/api/v1/chat/completions`). Its Responses endpoint (`/api/v1/responses`) is forwarded but not checked.

## 1. Start the proxy

```bash
uv run safeagents https://openrouter.ai/api --log-only
```

safeagents keeps the request path, so a harness asking for `/v1/chat/completions` reaches `https://openrouter.ai/api/v1/chat/completions`.

## 2. Point the harness at it

Use `http://127.0.0.1:8787/v1` wherever the harness takes an OpenAI-compatible base URL, with your OpenRouter key as the API key, and make sure it speaks Chat Completions:

- [pi](pi.md#which-providers-can-be-proxied) and [OMP](omp.md): declare a provider with `api: openai-completions`.
- [OpenCode](opencode.md), [Qwen Code](qwen-code.md), [Mistral Vibe](mistral-vibe.md), [OpenHands](openhands.md): their OpenAI-compatible provider settings.

## 3. Check it

Ask the agent to list the files in the repo. The safeagents terminal should show the request and a `PASS` line for the call.

## Notes

- **Force Chat Completions.** Some harnesses send OpenRouter traffic to the Responses API by default (OMP's built-in `openrouter` provider does). Those calls go through the proxy unchecked.
- **Keepalives.** While a model is queued, OpenRouter sends SSE comments such as `: OPENROUTER PROCESSING`. safeagents forwards them unchanged.
- **Errors after HTTP 200.** OpenRouter can end a stream with `data: {"error": {...}}` after the response has started. safeagents forwards the error and drops any tool call it was still holding, so a half-built call never reaches the harness.
- Model listing (`/api/v1/models`) and other non-chat requests pass through unchanged.

## Sources

- [Streaming](https://openrouter.ai/docs/api-reference/streaming): SSE comments and mid-stream errors
- [API reference](https://openrouter.ai/docs/api-reference/overview): base URL `https://openrouter.ai/api/v1`
