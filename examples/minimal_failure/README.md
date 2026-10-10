# Offline file-presence example

From the project checkout after installing AgentReducer:

```console
python examples/minimal_failure/run_demo.py
python examples/minimal_failure/run_demo.py --format json
```

The script copies four small fixture files into its own temporary directory,
creates an explicit local-Python verifier argv file and invokes the installed
CLI. The checker fails only when both `src/auth.py` and `src/cache.py` exist.
The result retains those two reducible files and separately pins `checker.py`.
No model, native Codex, credentials, network or test-module import is involved.
The original example and copied source bytes are checked after execution;
owned temporary files are cleaned up. This example is also included in the
source distribution and exercised after isolated wheel installation.
