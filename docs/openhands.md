# OpenHands

**Status: supported** with `anthropic/` models (Anthropic Messages) or `openai/` models forced onto Chat Completions.
Checked against the OpenHands Software Agent SDK v1.56.0, which requires LiteLLM >=1.93.0,<1.106 (tested with 1.93.0 and 1.105.7).

The OpenHands SDK calls models through LiteLLM, using the `base_url` set on its `LLM` object. Set that to safeagents.

For OpenAI models, also set `api_mode="chat"`. In `auto` mode, the SDK uses the OpenAI Responses API for any model that LiteLLM lists as supporting it, and safeagents doesn't guard that API. These models are affected:
- every `gpt-5*` and `gpt-6*` model, including the SDK's default, `gpt-5.6`;
- `gpt-4.1*`;
- `o3`, `o3-pro` and `o1-pro`.

## 1. Start the proxy

```bash
# Claude models
uv run safeagents --upstream https://api.anthropic.com --log-only

# OpenAI models, or any OpenAI-compatible server
uv run safeagents --upstream https://api.openai.com --log-only
```

## 2. Point the SDK at it

```python
import os
from pydantic import SecretStr
from openhands.sdk import LLM

# Anthropic Messages: no /v1, no trailing slash (LiteLLM appends /v1/messages)
llm = LLM(
    model="anthropic/claude-sonnet-4-5-20250929",
    base_url="http://127.0.0.1:8787",
    api_key=SecretStr(os.environ["ANTHROPIC_API_KEY"]),
)

# OpenAI Chat Completions: keep /v1 (LiteLLM appends /chat/completions)
llm = LLM(
    model="openai/gpt-4.1",
    base_url="http://127.0.0.1:8787/v1",
    api_key=SecretStr(os.environ["OPENAI_API_KEY"]),
    api_mode="chat",
)
```

Pass this `llm` to your `Agent` as usual.

`LLM.load_from_env()` reads the same settings from `LLM_`-prefixed environment variables:

```bash
export LLM_MODEL=openai/gpt-4.1
export LLM_BASE_URL=http://127.0.0.1:8787/v1
export LLM_API_KEY=sk-...
export LLM_API_MODE=chat
```

If you can't set `api_mode`, `capability_overrides={"supports_responses_api": False}` does the same.

## 3. Check it

Run a short conversation that uses a tool, for example the SDK's `01_hello_world.py` example. The safeagents terminal should show the request and a `PASS` line for the call.

## OpenHands CLI

The CLI pins an older SDK, 1.28.1. That version has no `api_mode`. It picks the Responses API by name alone, for models containing `gpt-5` or `codex-mini-latest`, so use a Claude model or an OpenAI model outside those names.

The CLI ignores environment variables unless you pass `--override-with-envs`:

```bash
LLM_MODEL=anthropic/claude-sonnet-4-5-20250929 \
LLM_BASE_URL=http://127.0.0.1:8787 \
LLM_API_KEY=$ANTHROPIC_API_KEY \
openhands --override-with-envs
```

The overrides apply to both the agent's model and the condenser model. Saved settings live in `~/.openhands/agent_settings.json`.

## Notes

- **The condenser, hooks, titles and the ask-agent use copies of the agent's `LLM`**, so they go through the proxy. A condenser you build yourself needs its own `base_url`.
- **Saved LLM profiles carry their own `base_url`.** Profiles live in `~/.openhands/profiles`. These features load them:
  - subagents with a `model` other than `inherit`;
  - `switch_llm`, `route_task_to_model` and the classify-and-switch tool;
  - Ask Oracle and `vision_inspect`.

  Point every profile you use at the proxy.
- **Other separate `LLM` objects need the proxy URL too:** `ToolShieldLLMSecurityAnalyzer`, the goal judge and fallback LLMs.
- **These bypass the proxy:**
  - `openhands/` models without a `base_url` (they use the All Hands proxy);
  - `digitalocean/` models;
  - ChatGPT subscription login (it talks to chatgpt.com, over the Responses API);
  - `ACPAgent`, which hands turns to an external agent such as Claude Code. Set that agent up separately.
- **Avoid `litellm_proxy/` model names.** With that prefix, the SDK fetches model info from `{base_url}/v1/model/info`, which goes to `/v1/v1/model/info` when `base_url` ends in `/v1`. The upstream's answer can also switch the model to the Responses API. Use `anthropic/` or `openai/`.
- **LiteLLM may download a newer model list at import time**, which can change which models it thinks support the Responses API. Setting `api_mode="chat"` makes that irrelevant. Alternatively, `LITELLM_LOCAL_MODEL_COST_MAP=True` pins LiteLLM to its bundled list.

## Sources

- [LLM architecture](https://docs.openhands.dev/sdk/arch/llm.md) and [`01_hello_world.py`](https://github.com/OpenHands/software-agent-sdk/blob/v1.56.0/examples/01_standalone_sdk/01_hello_world.py)
- [`llm.py` at v1.56.0](https://github.com/OpenHands/software-agent-sdk/blob/v1.56.0/openhands-sdk/openhands/sdk/llm/llm.py#L2849-L2854): `api_mode` and `uses_responses_api()`
- [`model_features.py` at v1.56.0](https://github.com/OpenHands/software-agent-sdk/blob/v1.56.0/openhands-sdk/openhands/sdk/llm/utils/model_features.py#L422-L437): how Responses support is decided
- [OpenHands CLI `pyproject.toml`](https://github.com/OpenHands/OpenHands-CLI/blob/main/pyproject.toml) (pins `openhands-sdk==1.28.1`) and [`argparsers/util.py`](https://github.com/OpenHands/OpenHands-CLI/blob/main/openhands_cli/argparsers/util.py): `--override-with-envs`
