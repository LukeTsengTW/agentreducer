"""Offline process fixture. Reads only declared fixture paths/environment."""

import json
import os
import sys
import time
from pathlib import Path


def record(name, value):
    destination = os.environ.get(name)
    if destination:
        with Path(destination).open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(value, ensure_ascii=False) + "\n")


args = sys.argv[1:]
if args == ["--version"] or args == ["--help"] or args == ["exec", "--help"]:
    record("AGENTREDUCER_FAKE_PREFLIGHT_COUNTER", args)
    if os.environ.get("AGENTREDUCER_FAKE_PREFLIGHT_MODE") == "timeout":
        print("partial help", flush=True)
        time.sleep(30)
    if os.environ.get("AGENTREDUCER_FAKE_PREFLIGHT_MODE") == "nonzero":
        raise SystemExit(2)
    if args == ["--version"]:
        print("codex fixture-1.0")
    else:
        flags = ["--ask-for-approval"] if args == ["--help"] else [
            "--model", "--sandbox", "--skip-git-repo-check", "--ephemeral",
            "--ignore-user-config", "--color",
        ]
        missing = os.environ.get("AGENTREDUCER_FAKE_MISSING_FLAG")
        print(" ".join(flag for flag in flags if flag != missing))
    raise SystemExit(0)

prompt = sys.stdin.buffer.read().decode("utf-8")
observation = {"argv": args, "cwd": str(Path.cwd()), "prompt": prompt, "pid": os.getpid()}
names = os.environ.get("AGENTREDUCER_FAKE_ENV_NAMES", "").split(",")
observation["env"] = {name: os.environ.get(name) for name in names if name}
record("AGENTREDUCER_FAKE_COUNTER", observation)
mode = os.environ.get("AGENTREDUCER_FAKE_MODE", "echo")
for name in os.environ.get("AGENTREDUCER_FAKE_ECHO_NAMES", "").split(","):
    if name:
        print(os.environ.get(name, ""), flush=True)
        print(os.environ.get(name, ""), file=sys.stderr, flush=True)
if mode == "timeout":
    print("partial stdout", flush=True)
    print("partial stderr", file=sys.stderr, flush=True)
    time.sleep(30)
elif mode == "invalid-utf8":
    os.write(sys.stdout.fileno(), b"invalid-\xff\n")
    os.write(sys.stderr.fileno(), b"error-\xfe\n")
elif mode in {"mutate", "prompt", "instructions", "smoke"}:
    if mode != "smoke":
        assert Path("state.txt").read_bytes() == b"original"
        assert not Path("trial-only.txt").exists()
        Path("state.txt").write_text("mutated", encoding="utf-8")
        Path("trial-only.txt").write_text("new", encoding="utf-8")
        text = Path("AGENTS.md").read_text(encoding="utf-8") if mode == "instructions" else prompt
        failing = mode == "mutate" or all(marker in text for marker in ("USE_CACHE", "AUTH_MODULE"))
        Path("generated.txt").write_text("bad" if failing else "good", encoding="utf-8")
    else:
        Path("arithmetic.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
elif mode == "nonzero":
    print("PASS: natural language must not classify this run")
    raise SystemExit(7)
elif mode == "echo":
    print(json.dumps(observation, ensure_ascii=False))
else:
    raise RuntimeError("Unknown fake mode")
