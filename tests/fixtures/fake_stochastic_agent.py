"""Offline child fixture: no provider, discovery, network or credentials."""

import json
import os
from pathlib import Path
import sys


def main():
    prompt = sys.stdin.buffer.read().decode("utf-8")
    domain = os.environ["AGENTREDUCER_STOCH_DOMAIN"]
    mode = os.environ["AGENTREDUCER_STOCH_MODE"]
    counter = Path(os.environ["AGENTREDUCER_STOCH_COUNTER"])
    ordinal = len(counter.read_text(encoding="utf-8").splitlines()) if counter.exists() else 0
    instructions = Path("AGENTS.md").read_text(encoding="utf-8")
    pristine = Path("state.txt").read_bytes() == b"original" and not Path("trial-only.txt").exists()
    with counter.open("a",encoding="utf-8") as stream:
        stream.write(json.dumps({"cwd":str(Path.cwd()),"prompt":prompt,
            "instructions":instructions,"ordinal":ordinal,"pristine":pristine}) + "\n")
    assert pristine
    if mode == "agent-error": return 2
    candidate = prompt if domain == "prompt" else instructions
    triggered = "USE_CACHE" in candidate and "AUTH_MODULE" in candidate
    bad = (mode == "all-fail" or mode != "all-pass" and triggered and ordinal % 5 != 2)
    Path("state.txt").write_bytes(b"mutated")
    Path("trial-only.txt").write_bytes(b"new")
    Path("generated.txt").write_text("bad" if bad else "good",encoding="utf-8",newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
