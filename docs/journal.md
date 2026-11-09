# Journal format

safeagents appends every call it doesn't pass to `~/.safeagents/calls.jsonl` (`--journal PATH` changes the path and `--no-journal` turns it off). Each line is one JSON object.

Note the file is created readable by its owner only (`0600`) - `arguments` and `task` can hold secrets.

| field | type | meaning |
| --- | --- | --- |
| `id` | int | call number, increasing within the file |
| `time` | float | Unix timestamp, in seconds |
| `verdict` | string | `blocked`: replaced by a notice, never ran. `flagged`: would have been blocked, but ran because of `--log-only`. `suspicious`: ran, with findings below `--block-severity` that the judge allowed or that no judge reviewed |
| `tool` | string | tool name |
| `arguments` | string | the call's raw JSON arguments, as the model sent them |
| `reason` | string or null | why the call was blocked or flagged; `null` for `suspicious` |
| `signals` | list | atif-scan findings: `{"id", "severity", "title"}`, with `severity` one of `low`, `medium`, `high`, `critical` |
| `judge` | string or null | the judge's answer, `allow: <why>`, `block: <why>` or `error: <why>`; `null` if no judge ran |
| `model` | string | the model named in the request |
| `task` | string | the text of the user turns, cut to the most recent part when long |

New fields may be added. Existing fields won't be renamed, removed or change meaning without a note in the release.
