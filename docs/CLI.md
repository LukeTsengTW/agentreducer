# AgentReducer CLI

Requires Python 3.11 or later. From this checkout, install with
`python -m pip install -e .`; development checks use `python -m pip install -e ".[dev]"`.
There are no runtime dependencies. Download the v0.1.0 wheel or source archive
from [GitHub Releases](https://github.com/LukeTsengTW/agentreducer/releases/tag/v0.1.0).
The [MIT license](../LICENSE) applies.

**Known live limitation:** three authorized AMBIENT trials failed to modify the
smoke fixture correctly: two gpt-6-luna trials, then gpt-6.1-sol with a changed
native executable. The cause remains unresolved; successful live Codex behavior
has not been demonstrated. The human waived the successful-live release
prerequisite only for v0.1.0; actual live validation remains FAILED. See
[release notes](releases/v0.1.0.md). Offline usage and tests do not consume model usage.

Both `agentreducer` and `python -m agentreducer` use the same main function.
Root options precede the required `prompt`, `instructions` or `repository`
subcommand. For example, `agentreducer --format json repository --help`.
Every run requires explicit `--source` and `--oracle command|codex`; there is no
implicit current-checkout reduction.

Run the independent local example from the checkout:

```console
python examples/minimal_failure/run_demo.py --format json
```

The following complete offline Python example runs all three domains in owned
temporary workspaces. Run it with the installed package. It uses only the local
Python interpreter and does not contact a model or read credentials.

```python
from pathlib import Path
import json
import subprocess
import sys
import tempfile

with tempfile.TemporaryDirectory(prefix="agentreducer-cli-guide-") as directory:
    root = Path(directory)
    for domain in ("prompt", "instructions", "repository"):
        source = root / domain
        source.mkdir()
        if domain == "repository":
            (source / "trigger.txt").write_text("marker", encoding="utf-8")
            check = "from pathlib import Path\nraise SystemExit(1 if Path('trigger.txt').exists() else 0)\n"
        else:
            check = "from pathlib import Path\nraise SystemExit(1 if 'TRIGGER' in Path('candidate.txt').read_text(encoding='utf-8') else 0)\n"
        (source / "checker.py").write_text(check, encoding="utf-8")
        vector = root / (domain + " argv.json")
        vector.write_text(json.dumps([sys.executable, "checker.py"]), encoding="utf-8")
        argv = [sys.executable, "-m", "agentreducer", "--format", "json", domain,
                "--source", str(source), "--oracle", "command",
                "--verify-argv-file", str(vector), "--fail-exit-code", "1"]
        if domain == "repository":
            argv += ["--pin", "checker.py", "--verifier-path", "checker.py"]
        else:
            argv += ["--candidate-path", "candidate.txt", "--text", "noise\n\nTRIGGER"]
        completed = subprocess.run(argv, shell=False, check=True, capture_output=True)
        report = json.loads(completed.stdout)
        assert report["status"] == "reduced"
        assert report["cost"]["agent_launches"]["value"] == 0
```

Use exactly one verifier vector: `--verify-argv-file PATH` reads a UTF-8 JSON
array, or `--verify-argv-json JSON` accepts that array directly. An explicit
argv file avoids Windows quoting ambiguity for paths containing spaces.
Arrays preserve later empty arguments, Unicode and backslashes; shell command
strings, interpolation and implicit argument splitting are unsupported.
`--verify-timeout` defaults to 60 finite positive seconds. Repeated
`--pass-exit-code` values replace the default `{0}`. Without explicit
`--fail-exit-code`, M3 treats every non-pass nonzero exit as FAIL. Declare fail
codes when other exits should be infrastructure ERROR. Timeout remains ERROR.

Prompt and instruction inputs select exactly one `--input-file PATH`, `--stdin`
or `--text TEXT`. All byte inputs are strict UTF-8. Prompt `--split paragraph`
is the default; `--split line` retains nonblank lines as units. Command prompt
requires `--candidate-path`; instructions default to `AGENTS.md`. Paths must be
canonical portable relative file paths. Codex prompt uses the reduced text as
the task and forbids `--candidate-path`. Codex instructions/repository require
one fixed `--task-file`, `--task-stdin` or `--task-text`; task and candidate cannot
both consume stdin. Command mode forbids agent/task options.

Repository runs select whole-file presence from an initial fixed-byte manifest.
Repeated `--pin` paths remain fixed context, separate from reducible files.
Every `--verifier-path` dependency must be pinned. No CLI overlays or full-tree
export are provided in v0.1. `--exclude-basename` adds to fixed default exclusions
(`.git`, `.venv`, `__pycache__`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache`,
`node_modules`) at every depth; there is no gitignore interpretation.
Source drift is detected before subsequent copied execution.

Repetition requires `--stochastic` plus all of `--min-trials`, `--max-trials`,
`--failure-threshold` and `--max-total-trials`. Counts are strict positive
integers, min <= max and budget >= min. Thresholds such as `4/5` or `0.80` are
exact fractions. M5 is an empirical finite-horizon policy with observed
policy-relative 1-minimality, no confidence/iid/global minimum guarantee.
Its budget bounds trial attempts, not opaque backend model requests.

Codex mode additionally requires `--allow-agent-execution`, explicit `--model`
and `--context-mode ambient`. `controlled` fails closed because native isolation
is unavailable. Optional `--codex-executable` is absolute; alternatively supply
an explicit `--codex-argv-file`/`--codex-argv-json` vector. Default native discovery
occurs only after full validation. No preflight/login/credential bridge occurs.
`--agent-timeout` defaults to 120 seconds. Agent execution can make model
requests; this guide and offline gates never execute that path. One agent
process is not proof of one backend model call.

Normal human output is default; `--format json` selects schema1. `--output PATH`
always publishes a JSON report. `--reduced-output PATH` instead requests raw
successful reduced text or a reducible path list. These destinations are
mutually exclusive. Parents must already exist. Existing destinations are
rejected unless root `--force` is supplied; source descendants, protected
input/argv aliases, directories, links and reparse points are rejected even
with force. Config/parse failures create no output; run errors may publish a
result-null JSON report but never a partial reduced artifact.

Publication uses an owned same-directory temporary file, flush/fsync/close,
then an atomic no-clobber hardlink; only force uses replacement. Unsupported
hardlinks fail closed. A concurrent creator is preserved. No power-loss,
adversarial-race or multi-file transaction guarantee is made. Publication
precedes stdout; if stdout fails, a complete published artifact remains.
`OUTPUT_PUBLISH_FAILED` includes the hardlink requirement: choose a supported
destination filesystem, or explicitly opt into replacement with `--force`.
`OUTPUT_PUBLISHED_CLEANUP_FAILED` confirms that the complete destination was
published and retained even though owned temporary-file cleanup failed. Other
output errors do not confirm publication. An established cancellation keeps
exit 130 if publishing its error report also fails; a bounded stderr diagnostic
records that secondary delivery failure. Cancellation identity also survives
ordinary stream-close or temporary-file cleanup failures.

| Exit | Meaning |
| ---: | --- |
| 0 | Reduction complete, or help/version |
| 2 | Invalid arguments/input/configuration/preparation |
| 3 | Initial classification PASS |
| 4 | Deterministic oracle ERROR |
| 5 | Stochastic ERROR; budget and trial failures have distinct codes |
| 6 | Unexpected internal failure |
| 7 | Publication/stdout delivery failure |
| 130 | KeyboardInterrupt after workspace unwind |

Runtime SystemExit and other BaseExceptions preserve their identity. `--debug`
prints only exception type and frame basename/line/function, with no message,
source line, locals, chains or cleanup notes. See [reports](REPORTS.md) and
[security boundaries](SECURITY.md) before interpreting a result.
