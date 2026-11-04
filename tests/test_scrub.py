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
from types import SimpleNamespace
from typing import ClassVar

import huggingface_hub
import pytest

from safeagents import Scrubber
from safeagents.cli import main
from safeagents.scrub import environment_secrets

KEY = "sk-proj-abcdefgh12345678ijklmnop"
TRACE = {
    "schema_version": "ATIF-v1.7",
    "steps": [
        {"step_id": 1, "source": "user", "message": "Fix the tests, then mail alice.doe@acme-corp.io"},
        {
            "step_id": 2,
            "source": "agent",
            "message": "",
            "tool_calls": [{"tool_call_id": "a", "function_name": "bash", "arguments": {"command": "env"}}],
            "observation": {
                "results": [{"source_call_id": "a", "content": f"OPENAI_API_KEY={KEY}\nHOME=/Users/alice"}]
            },
        },
        {"step_id": 3, "source": "agent", "message": f"Using {KEY} against 8.8.4.4"},
    ],
}


def write_trace(tmp_path, trace=TRACE):
    path = tmp_path / "runs" / "trace.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(trace), encoding="utf-8")
    return path


def test_credentials_are_masked_wherever_they_reappear():
    redacted, findings = Scrubber(environment=False).scrub(TRACE, "trace.json")
    text = json.dumps(redacted)
    assert KEY not in text
    assert redacted["steps"][2]["message"] == "Using *** against <ip>"  # the bare repeat too
    assert {finding.where for finding in findings} == {
        "steps[0].message",
        "steps[1].observation.results[0].content",
        "steps[2].message",
    }
    assert all(KEY not in finding.preview for finding in findings)


def test_personal_information():
    scrubber = Scrubber(environment=False)
    assert scrubber.redact_text("mail alice.doe@acme-corp.io") == ("mail <email>", ["email"])
    assert scrubber.redact_text("cd /home/alice/src && ls C:\\Users\\bob") == (
        "cd /home/<user>/src && ls C:\\Users\\<user>",
        ["home directory"],
    )
    assert scrubber.redact_text("curl 8.8.4.4") == ("curl <ip>", ["ip address"])
    # Public, generic or local: kept.
    kept = "git@github.com:org/repo noreply@anthropic.com /home/ubuntu 10.0.0.2 127.0.0.1 numpy==1.26.4"
    assert scrubber.redact_text(kept) == (kept, [])
    assert Scrubber(environment=False, pii=False).redact_text("mail alice.doe@acme-corp.io")[1] == []


def test_environment_and_listed_values(monkeypatch):
    monkeypatch.setenv("MY_SERVICE_TOKEN", "opaqueValue2026xyz")
    monkeypatch.setenv("MAX_TOKENS", "4096")
    assert "opaqueValue2026xyz" in environment_secrets()
    assert "4096" not in environment_secrets()

    redacted, kinds = Scrubber(["Nebula"]).redact_text("project Nebula, printed opaqueValue2026xyz")
    assert redacted == "project ***, printed ***"
    assert kinds == ["listed value", "environment secret"]
    assert Scrubber(environment=False).redact_text("printed opaqueValue2026xyz")[1] == []


def test_json_lines_stay_one_record_per_line(tmp_path):
    path = tmp_path / "session.jsonl"
    lines = [{"role": "user", "content": "token hf_abcdefghijklmnopqrstuvwxyz12"}, {"role": "assistant"}, "not json"]
    path.write_text("\n".join(json.dumps(line) if isinstance(line, dict) else line for line in lines))
    redacted, findings = Scrubber(environment=False).scrub_file(path)
    records = redacted.splitlines()
    assert json.loads(records[0]) == {"role": "user", "content": "token ***"}
    assert records[2] == "not json"
    assert [finding.where for finding in findings] == ["line 1: content"]


def test_clean_files_are_left_byte_for_byte(tmp_path):
    path = tmp_path / "clean.json"
    path.write_text('{"a":   "nothing to see"}')
    assert Scrubber(environment=False).scrub_file(path) == ('{"a":   "nothing to see"}', [])


def test_scrub_reports_and_writes_redacted_copies(tmp_path, capsys):
    trace = write_trace(tmp_path)
    out = tmp_path / "out"
    with pytest.raises(SystemExit) as exit_info:
        main(["scrub", str(trace.parent), "--no-env", "--out", str(out)])
    assert exit_info.value.code == 1
    report = capsys.readouterr().out
    assert "runs/trace.json  steps[0].message  email" in report
    assert KEY not in report
    copy = json.loads((out / "runs" / "trace.json").read_text())
    assert copy["steps"][0]["message"] == "Fix the tests, then mail <email>"

    main(["scrub", str(out), "--no-env"])  # nothing left: exits normally
    assert "0 strings to redact in 1 files" in capsys.readouterr().out


class FakeApi:
    uploads: ClassVar[list] = []

    def create_repo(self, repo_id, **options):
        self.uploads.append(("create", repo_id, options))

    def upload_folder(self, *, folder_path, **options):
        files = {path.name: path.read_text() for path in folder_path.rglob("*") if path.is_file()}
        self.uploads.append(("upload", files, options))
        return SimpleNamespace(commit_url="https://huggingface.co/datasets/me/traces/commit/1")


def test_publish_uploads_only_redacted_copies(tmp_path, monkeypatch, capsys):
    FakeApi.uploads = []
    monkeypatch.setattr(huggingface_hub, "HfApi", FakeApi)
    trace = write_trace(tmp_path)
    main(["publish", str(trace), "--repo", "me/traces", "--no-env", "--yes"])

    (_, repo, create_options), (_, files, upload_options) = FakeApi.uploads
    assert repo == "me/traces"
    assert create_options == {"repo_type": "dataset", "private": True, "exist_ok": True}
    assert upload_options["repo_id"] == "me/traces"
    assert list(files) == ["trace.json"]
    assert KEY not in files["trace.json"] and "alice" not in files["trace.json"]
    assert "uploaded: https://huggingface.co/datasets/me/traces/commit/1" in capsys.readouterr().out
    assert json.loads(trace.read_text()) == TRACE  # the original is untouched


def test_publish_dry_run_and_refusal_upload_nothing(tmp_path, monkeypatch):
    FakeApi.uploads = []
    monkeypatch.setattr(huggingface_hub, "HfApi", FakeApi)
    trace = write_trace(tmp_path)
    main(["publish", str(trace), "--repo", "me/traces", "--no-env", "--dry-run"])
    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    with pytest.raises(SystemExit, match="nothing uploaded"):
        main(["publish", str(trace), "--repo", "me/traces", "--no-env"])
    assert FakeApi.uploads == []
