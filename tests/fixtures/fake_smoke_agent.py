"""Owned local protocol fixture; never invokes Codex or a model."""

import hashlib
import json
from pathlib import Path
import sys

case, event_file, *args = sys.argv[1:]
CANARY = "M7_OBS_PRIVATE_CANARY"


def record(data):
    with Path(event_file).open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(data) + "\n")


if args in (["--version"], ["--help"], ["exec", "--help"]):
    record({"phase": "fake-preflight"})
    if case == "preflight-error":
        print(CANARY + str(Path.cwd()), file=sys.stderr)
        raise SystemExit(23)
    print("codex-cli offline-fixture" if args == ["--version"] else
          "--ask-for-approval --model --sandbox --skip-git-repo-check --ephemeral --ignore-user-config --color")
    raise SystemExit(0)

assert args == ["--ask-for-approval", "never", "exec", "--model", "gpt-6-luna",
                "--sandbox", "workspace-write", "--skip-git-repo-check",
                "--ephemeral", "--ignore-user-config", "--color", "never", "-"]
task = sys.stdin.read()
assert task == "Fix arithmetic.py so add(a, b) returns the sum. Change only that file. Do not install anything or access the network."
target = Path("arithmetic.py")
assert target.read_bytes() == b"def add(a, b):\n    return a - b\n"
record({"phase": "fake-agent", "task_sha256": hashlib.sha256(task.encode()).hexdigest()})
print(CANARY + str(Path.cwd()))
print(CANARY + " stderr", file=sys.stderr)
if case == "correct":
    target.write_bytes(b"def add(a, b):\n    return a + b\n")
elif case == "correct-format":
    target.write_bytes(b"print('M7_OBS_PRIVATE_CANARY verifier')\r\ndef add(a,b):\r\n\treturn (a+b)\r\n")
elif case == "agent-error":
    raise SystemExit(23)
elif case == "wrong-file":
    Path("other.py").write_bytes(b"def add(a, b):\n    return a + b\n")
elif case == "wrong-content":
    target.write_bytes(b"def add(a, b):\n    return a * b\n")
else:
    assert case == "no-op"
