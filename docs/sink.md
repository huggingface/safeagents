# Optional event sinks

A tool call withheld by the proxy never reaches the harness's execution hooks.
A sink can report that attempted call to an audit system without polling the
journal. It receives `blocked`, `flagged` and `suspicious` observations. Calls
with verdict `passed` are not delivered.

Sinks are off by default and add no dependencies. SafeAgents does not provide
an HTTP destination, read sink credentials, retry delivery or translate events
into another product's schema.

## Enable a trusted callback

Save this as `metadata_sink.py`:

```python
import json


def sink():
    def handle(entry: dict) -> None:
        # Select fields deliberately. Do not print the whole entry.
        observation = {
            "time": entry["time"],
            "journal_id": entry["id"],
            "verdict": entry["verdict"],
            "tool": entry["tool"],
        }
        print(json.dumps(observation), flush=True)

    return handle
```

```sh
safeagents start http://127.0.0.1:8080 --sink ./metadata_sink.py:sink
```

`--sink MODULE:FACTORY` follows the `--plugin` loading convention. A file path
also works, and the option is repeatable. `FACTORY()` runs at startup and must
return a synchronous callable accepting one dict. Async callbacks are rejected.
Factories and callbacks are trusted local code with the proxy's permissions,
not sandboxed extensions. Only load code you trust.

From Python, pass callbacks directly: `Config(sinks=[handle])`. If you use
`Guard` directly, call `config.close_sinks()` when finished. The proxy and CLI
close sink delivery on shutdown.

## Payload and privacy

The dict has the fields in [journal.md](journal.md). With a journal, its `id`
and `time` match the journal record. With `--no-journal`, `id` is `None` and
sink delivery still works. The journal ID is local to that journal file,
not a globally unique event ID or a session ID.

**Enabling a sink exposes the full journal-shaped payload to the callback.**
`arguments`, `task`, `reason`, signal titles and judge text may contain secrets
or personal data. `--no-journal` disables the local journal, not these callback
fields. SafeAgents does not redact them for a sink. The example above selects
metadata, but even a tool name may be sensitive. Review access, retention,
redaction and any network destination before adding your own exporter.

Each sink receives its own copy. Editing it cannot change another sink's copy,
the journal record or the guard's decision. A notification describes a proxy
decision about a proposed call. Being allowed or flagged does not prove that
the harness executed the call, and the sink does not verify who used an account.

## Delivery limits

Callbacks run on daemon threads, outside the response event loop. Each sink
has one in-flight callback and a queue of at most 16 pending observations.
Enqueueing never waits for callback I/O. A failing callback produces a warning
without its exception text and is not retried. A slow or failing sink does not
hold up another sink.

Callbacks still share the proxy's process and Python runtime. This isolates
callback I/O from response handling, not CPU use or arbitrary unsafe code.
It is not a hard response-latency guarantee.

After 250 ms, a slow callback produces a deadline warning. **This is not forced
cancellation:** Python cannot kill a callback thread. Its worker waits off the
response path until it returns; the bounded queue can fill and later
notifications can be dropped. Give your callback's network or file operations
their own bounded timeouts. SafeAgents reports queue overflow without logging
the payload.

Delivery is best effort, with no durability or exactly-once guarantee. Ordering
is per sink, not across sinks. Shutdown stops accepting work and discards queued
observations without flushing; an already running callback may continue until
it returns. A process exit can lose notifications. Keep the journal when you
need a local record to reconcile against.

## Use cases

- **Agentmetry alongside SafeAgents:** a custom adapter could record the
  proxy's denied attempts next to hook-reported calls that reached an agent.
  Keep the sources and outcomes distinct. Do not turn a blocked proposal into
  a successful `tool_called` event, infer a session from prompt contents, or
  treat a journal-local ID as an Agentmetry event UUID. This patch supplies the
  callback boundary, not an Agentmetry adapter or a tested end-to-end integration.
- **SIEM notifications:** an operator-owned sink could select and redact a
  small observation, then submit it using its own credential and timeout.
  Plan for drops, duplicates and destination outages before relying on it.
- **Local counters:** count verdicts without retaining conversation contents
  or tool arguments. A missing notification is not evidence that no attempted
  call occurred.
