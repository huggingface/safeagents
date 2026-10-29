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
import json
import socket

import pytest

from safeagents.cli import main
from safeagents.runs import run_dir

UPSTREAM = "http://127.0.0.1:9"


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_start_ls_stop(capsys):
    port = free_port()
    main(["start", UPSTREAM, str(port), "--no-scan", "--no-journal"])
    assert capsys.readouterr().out.startswith(f"1  http://127.0.0.1:{port} -> {UPSTREAM}")
    try:
        main(["ls"])
        listing = capsys.readouterr().out
        assert listing.startswith("1 ")
        assert f"http://127.0.0.1:{port} -> {UPSTREAM}" in listing
        assert "--no-scan --no-journal" in listing

        with pytest.raises(SystemExit, match="already in use"):
            main(["start", UPSTREAM, str(port)])
    finally:
        main(["stop", "1"])
    assert capsys.readouterr().out.startswith("stopped 1")

    main(["ls"])
    assert capsys.readouterr().out == "no proxies running\n"
    assert (run_dir() / "1.log").exists()  # kept for debugging


def test_start_reports_why_the_proxy_failed():
    with pytest.raises(SystemExit) as exit_info:
        main(["start", UPSTREAM, str(free_port()), "--no-such-option"])
    assert "didn't start" in str(exit_info.value.code)
    assert "unrecognized arguments: --no-such-option" in str(exit_info.value.code)
    assert not list(run_dir().glob("*.json"))


def test_ls_forgets_proxies_that_died(capsys):
    run_dir().mkdir(parents=True)
    record = {"id": 3, "pid": 1, "upstream": UPSTREAM, "listen": "127.0.0.1:1", "options": [], "started": 0}
    (run_dir() / "3.json").write_text(json.dumps(record))
    main(["ls"])
    assert capsys.readouterr().out == "no proxies running\n"
    assert not (run_dir() / "3.json").exists()


def test_stop_unknown_proxy():
    with pytest.raises(SystemExit, match="no proxy 4 is running"):
        main(["stop", "4"])
