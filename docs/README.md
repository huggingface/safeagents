# Using safeagents with popular harnesses

Each page below shows how to put safeagents between one agent harness and its model server. They cover the ten most popular harnesses tracked by [Harness Lens](https://huggingface.co/spaces/ybelkada/harness-lens), ranked by the GitHub stars of their repositories on 2026-10-29.

safeagents checks tool calls in **OpenAI Chat Completions** and **Anthropic Messages** traffic. Every guide follows the same three steps:

1. Start `safeagents --upstream <the model server the harness normally uses>`.
2. Set the harness's base URL to `http://127.0.0.1:8787`, with or without `/v1` depending on the harness.
3. Make sure every model the harness uses goes through the proxy, including the ones for titles, summaries, compaction and subagents.

Harnesses that only speak the OpenAI Responses API or the Gemini API can be routed through the proxy, but their tool calls aren't checked yet.

| # | Harness | Protocol through safeagents | Status |
| --- | --- | --- | --- |
| 1 | [OpenClaw](openclaw.md) | Anthropic Messages, Chat Completions | supported |
| 2 | [DeepSeek Harness](deepseek-harness.md) | Anthropic Messages, Chat Completions | supported (API key) |
| 3 | [Hermes Agent](hermes-agent.md) | Anthropic Messages, Chat Completions | supported |
| 4 | [OpenCode](opencode.md) | Anthropic Messages, Chat Completions | supported |
| 5 | [GitHub Copilot (VS Code)](copilot-vscode.md) | Anthropic Messages, Chat Completions | BYOK models only |
| 6 | [Claude Code](claude-code.md) | Anthropic Messages | supported |
| 7 | [Codex CLI](codex.md) | Responses API only | not supported yet |
| 8 | [pi](pi.md) | Anthropic Messages, Chat Completions | supported |
| 9 | [Gemini CLI](gemini-cli.md) | Gemini API only | not supported |
| 10 | [Cline](cline.md) | Anthropic Messages, Chat Completions | supported |

Each page names the harness version it was checked against. The guides come from reading each harness's documentation and source code at that version. They haven't been run end to end, so start with `--log-only` and watch the proxy's terminal before you turn blocking on.
