# Copyright 2026-present the HuggingFace Inc. team.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Synthetic observations only; no model, harness or tool execution."""

import asyncio
import threading
import time
from contextlib import nullcontext

import httpx
import pytest
from atif_scan import Severity
from conftest import bash_request

from safeagents import Config, Guard
from safeagents.journal import Journal, read
from safeagents.scan import Signal


@pytest.fixture
def configure():
    configs = []

    def make(**kwargs):
        config = Config(scanner=None, **kwargs)
        configs.append(config)
        return config

    yield make
    for config in configs:
        config.close_sinks()


def receive():
    entries = []
    arrived = threading.Event()

    def callback(entry):
        entries.append(entry)
        arrived.set()

    return callback, entries, arrived


def allow(config, tool="undeclared", args="{}"):
    return asyncio.run(Guard(bash_request("list the files"), config).allow(tool, args))


@pytest.mark.parametrize(("block", "verdict", "allowed"), [(True, "blocked", False), (False, "flagged", True)])
def test_block_and_log_only_report_the_same_harmless_proposal(configure, block, verdict, allowed):
    sink, entries, arrived = receive()
    config = configure(sinks=[sink], block=block)
    assert allow(config) is allowed
    assert arrived.wait(1)
    assert entries[0]["verdict"] == verdict
    assert entries[0]["id"] is None
    assert entries[0]["tool"] == "undeclared"
    assert entries[0]["arguments"] == "{}"


def test_passed_call_does_not_enqueue_but_next_blocked_call_does(configure):
    sink, entries, arrived = receive()
    config = configure(sinks=[sink])
    assert allow(config, "bash", '{"command":"pwd"}')
    assert not allow(config)
    assert arrived.wait(1)
    assert [entry["tool"] for entry in entries] == ["undeclared"]


def test_sink_entry_matches_the_journal_including_id_and_timestamp(configure, tmp_path):
    sink, entries, arrived = receive()
    journal = Journal(tmp_path / "calls.jsonl")
    config = configure(sinks=[sink], journal=journal)
    assert not allow(config)
    assert arrived.wait(1)
    assert entries == read(journal.path)
    assert entries[0]["id"] == 1
    assert isinstance(entries[0]["time"], float)
    assert set(entries[0]) == {
        "id",
        "time",
        "verdict",
        "tool",
        "arguments",
        "reason",
        "signals",
        "judge",
        "model",
        "task",
    }


def test_suspicious_observation_is_delivered_without_changing_allowance(configure):
    class Scanner:
        def signals(self, *_):
            return [Signal("synthetic.low", Severity.LOW, "Synthetic low finding")]

    sink, entries, arrived = receive()
    config = configure(sinks=[sink])
    config.scanner = Scanner()
    assert allow(config, "bash", '{"command":"pwd"}')
    assert arrived.wait(1)
    assert entries[0]["verdict"] == "suspicious"
    assert entries[0]["signals"] == [{"id": "synthetic.low", "severity": "low", "title": "Synthetic low finding"}]


def test_callbacks_receive_independent_copies_and_cannot_change_the_journal(configure, tmp_path):
    mutated = threading.Event()

    def mutate(entry):
        entry["signals"].append({"id": "synthetic.mutation"})
        entry["verdict"] = "passed"
        mutated.set()

    other, entries, arrived = receive()
    journal = Journal(tmp_path / "calls.jsonl")
    config = configure(sinks=[mutate, other], journal=journal)
    assert not allow(config)
    assert mutated.wait(1) and arrived.wait(1)
    assert entries == read(journal.path)
    assert entries[0]["signals"] == []
    assert entries[0]["verdict"] == "blocked"


@pytest.mark.parametrize("error", [RuntimeError, SystemExit, KeyboardInterrupt])
def test_failing_sink_does_not_leak_exception_text_or_block_other_sinks(configure, caplog, error):
    failed = threading.Event()

    def fail(entry):
        failed.set()
        raise error("SYNTHETIC_PRIVATE_DETAIL")

    other, entries, arrived = receive()
    config = configure(sinks=[fail, other])
    assert not allow(config)
    assert failed.wait(1) and arrived.wait(1)
    assert "SYNTHETIC_PRIVATE_DETAIL" not in caplog.text
    assert entries[0]["verdict"] == "blocked"


def test_slow_sink_is_off_loop_bounded_and_does_not_block_a_healthy_sink(configure, monkeypatch, caplog):
    from safeagents import sinks

    monkeypatch.setattr(sinks, "QUEUE_SIZE", 1)
    monkeypatch.setattr(sinks, "CALL_TIMEOUT", 0.02)
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def slow(entry):
        calls.append(entry)
        entered.set()
        release.wait(5)

    other, entries, arrived = receive()
    config = configure(sinks=[slow, other])
    try:
        assert not allow(config)
        assert entered.wait(1) and arrived.wait(1)

        async def many():
            for _ in range(20):
                assert not await Guard(bash_request("list files"), config).allow("undeclared", "{}")

        asyncio.run(many())
        worker = config._sink_dispatcher._workers[0]
        assert worker.queue.qsize() <= 1
        assert len(calls) == 1  # A timed-out callback never creates another in-flight callback.
        deadline = time.monotonic() + 1
        while "callback deadline" not in caplog.text and time.monotonic() < deadline:
            time.sleep(0.01)
        assert "callback deadline" in caplog.text
        assert entries
    finally:
        release.set()


def test_closing_discards_pending_work_and_rejects_new_notifications(configure):
    entered = threading.Event()
    release = threading.Event()

    def slow(entry):
        entered.set()
        release.wait(5)

    config = configure(sinks=[slow])
    try:
        assert not allow(config)
        assert entered.wait(1)
        assert not allow(config)
        config.close_sinks()
        assert not allow(config)
        worker = config._sink_dispatcher._workers[0]
        assert worker.closed.is_set()
        assert worker.queue.empty()
    finally:
        release.set()


@pytest.mark.parametrize("block", [True, False])
def test_proxy_response_and_sink_verdict_agree_without_a_model(configure, block):
    from safeagents.proxy import create_app

    sink, entries, arrived = receive()
    config = configure(sinks=[sink], block=block)
    body = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "synthetic-call",
                            "type": "function",
                            "function": {"name": "undeclared", "arguments": "{}"},
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ]
    }
    app = create_app(
        "http://scripted.invalid", config, transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body))
    )

    async def request():
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://proxy.invalid") as client,
        ):
            response = await client.post("/v1/chat/completions", json=bash_request("list files"))
            assert arrived.wait(1)
            return response

    response = asyncio.run(request())
    assert response.status_code == 200 and arrived.wait(1)
    message = response.json()["choices"][0]["message"]
    assert bool(message.get("tool_calls")) is not block
    assert entries[0]["verdict"] == ("blocked" if block else "flagged")
    assert all(worker.closed.is_set() for worker in config._sink_dispatcher._workers)


def test_load_file_and_module_factories(tmp_path, monkeypatch):
    from safeagents.sinks import load_sink

    source = tmp_path / "synthetic_sink.py"
    source.write_text("def sink():\n    return lambda entry: None\n")
    assert callable(load_sink(f"{source}:sink"))
    monkeypatch.syspath_prepend(str(tmp_path))
    assert callable(load_sink("synthetic_sink:sink"))
    with pytest.raises(ValueError):
        load_sink("missing_factory")
    source.write_text("def bad():\n    return 42\n")
    with pytest.raises(TypeError):
        load_sink(f"{source}:bad")
    source.write_text("async def sink():\n    return lambda entry: None\n")
    with pytest.raises(TypeError, match="factory must be synchronous"):
        load_sink(f"{source}:sink")


def test_async_callbacks_are_rejected_at_configuration():
    async def callback(entry):
        pass

    with pytest.raises(TypeError, match="synchronous callable"):
        Config(scanner=None, sinks=[callback])


def test_cli_loads_repeatable_sinks_even_without_a_journal(tmp_path, monkeypatch):
    from safeagents.cli import main

    source = tmp_path / "sink.py"
    source.write_text("def sink():\n    return lambda entry: None\n")
    captured = []
    monkeypatch.setattr("safeagents.proxy.create_app", lambda url, config: captured.append(config) or object())
    monkeypatch.setattr("safeagents.monitor.Monitor", lambda **kwargs: nullcontext(None))
    monkeypatch.setattr("uvicorn.run", lambda *args, **kwargs: None)
    main(
        [
            "http://scripted.invalid",
            "18080",
            "--no-scan",
            "--no-journal",
            "--sink",
            f"{source}:sink",
            "--sink",
            f"{source}:sink",
        ]
    )
    config = captured[0]
    assert config.journal is None and len(config.sinks) == 2
    assert all(worker.closed.is_set() for worker in config._sink_dispatcher._workers)


def test_default_config_has_no_dispatcher():
    assert Config(scanner=None)._sink_dispatcher is None
