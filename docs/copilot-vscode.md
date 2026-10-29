# GitHub Copilot (VS Code agent)

**Status: supported for bring-your-own-key (BYOK) models only**, through the *Custom Endpoint* provider (Anthropic Messages or OpenAI Chat Completions).
Checked against VS Code 1.142.0.

Models served by GitHub Copilot itself can't be routed through safeagents. They come from the Copilot API, which has no supported base-URL setting. What you can proxy are models you add with your own API key under the **Custom Endpoint** provider, which takes a full URL per model.

## 1. Start the proxy

```bash
# Claude models
uv run safeagents https://api.anthropic.com --log-only

# OpenAI models, or any OpenAI-compatible server
uv run safeagents https://api.openai.com --log-only
```

## 2. Add the models in VS Code

Run *Chat: Manage Language Models* → *Add Models* → *Custom Endpoint*. VS Code opens `chatLanguageModels.json`. Add one group per upstream:

```json
[
  {
    "name": "safeagents-anthropic",
    "vendor": "customendpoint",
    "apiKey": "${input:anthropicApiKey}",
    "apiType": "messages",
    "models": [
      {
        "id": "claude-sonnet-4-6",
        "name": "Claude Sonnet 4.6 (safeagents)",
        "url": "http://127.0.0.1:8787/v1/messages",
        "toolCalling": true,
        "vision": true,
        "maxInputTokens": 200000,
        "maxOutputTokens": 64000
      }
    ]
  },
  {
    "name": "safeagents-openai",
    "vendor": "customendpoint",
    "apiKey": "${input:openaiApiKey}",
    "apiType": "chat-completions",
    "models": [
      {
        "id": "gpt-4.1",
        "name": "GPT-4.1 (safeagents)",
        "url": "http://127.0.0.1:8787/v1/chat/completions",
        "toolCalling": true,
        "vision": true,
        "maxInputTokens": 1000000,
        "maxOutputTokens": 32768
      }
    ]
  }
]
```

- **`apiType` must be `messages` or `chat-completions`.** `responses` sends traffic to `/v1/responses`, which safeagents doesn't guard. If `apiType` is unset and the URL has no API path, Copilot defaults to Chat Completions.
- **Give the full `url` per model.** A URL that already ends in `/messages` or `/chat/completions` is used as is.
- **List models under `models`; don't rely on discovery.** A provider-level `url` makes Copilot fetch `/models` and drop models it doesn't recognise.
- **`apiKey` is required.** With `messages`, Copilot sends it as `x-api-key`; with `chat-completions`, as `Authorization: Bearer`. safeagents forwards both.

Then pick the `(safeagents)` model in the chat model picker and switch to Agent mode.

## 3. Route the utility tasks too

Copilot sends titles, summaries, commit messages and intent detection to a separate *utility* model. By default (`chat.byokUtilityModelDefault: "copilot"`), that model is a Copilot API model, which bypasses the proxy. Keep them on your proxied model:

```json
"chat.byokUtilityModelDefault": "mainAgent"
```

You can also point `chat.utilityModel` and `chat.utilitySmallModel` at the safeagents models.

## 4. Check it

In Agent mode, ask Copilot to list the files in the workspace. The safeagents terminal should show the request and a `PASS` line for the tool call.

## Notes

- With BYOK models, Copilot turns off its search, explore and execution subagents and semantic codebase search. Inline completions and embeddings still use GitHub's service.
- BYOK works when signed out. When you're signed in, it needs an individual or internal plan, or a Business/Enterprise plan with the BYOK policy on.
- The built-in **OpenAI** and **OpenRouter** BYOK providers have hard-coded URLs, and the OpenAI one always uses the Responses API. Use Custom Endpoint instead.
- The built-in **Anthropic** BYOK provider has no URL setting. It may read `ANTHROPIC_BASE_URL` from the environment VS Code was launched in, but this is untested. Prefer Custom Endpoint.
- A custom agent or the Plan agent that names a Copilot-hosted model bypasses the proxy.
- `github.copilot.chat.customOAIModels` is deprecated and gets migrated to the Custom Endpoint format.

## Sources

- [Language models in VS Code](https://code.visualstudio.com/docs/agent-customization/language-models): custom endpoint models, URL resolution, utility models
- [`customEndpointProvider.ts` at 1.142.0](https://github.com/microsoft/vscode/blob/1.142.0/extensions/copilot/src/extension/byok/vscode-node/customEndpointProvider.ts): `apiType` handling and auth headers
- [`agentIntent.ts` at 1.142.0](https://github.com/microsoft/vscode/blob/1.142.0/extensions/copilot/src/extension/intents/node/agentIntent.ts#L238-L247): features turned off on BYOK
- [`chat.shared.contribution.ts` at 1.142.0](https://github.com/microsoft/vscode/blob/1.142.0/src/vs/workbench/contrib/chat/browser/chat.shared.contribution.ts#L1908-L1924): `chat.byokUtilityModelDefault`
