# Execution and experimental boundaries

**Known live limitation for v0.1.0:** three authorized real AMBIENT trials failed
to correct the smoke fixture: two gpt-6-luna runs and one gpt-6.1-sol run with a
changed native executable. Source preservation and workspace cleanup passed;
successful live Codex behavior has not been demonstrated. The cause is unresolved,
and the changed executable prevents a model-specific comparison. Offline Codex
composition is validated. The human waived successful live validation only as a
v0.1.0 release prerequisite; actual LIVE_VALIDATION remains FAILED. This waiver
does not establish backend request bounds or filesystem isolation. See the
[release notes](releases/v0.1.0.md) for the evidence and release-policy distinction.

- Verifiers and agents execute arbitrary code with their process user's host
  permissions. Use trusted programs and suitable OS isolation for untrusted
  work. shell=False and explicit argv avoid shell interpolation; they do not
  make the invoked code safe. Batch-file shell paths are rejected by the native
  adapter; no automatic shell fallback is added.
- Workspaces are disposable copies of a trusted, quiescent source. Exclusions
  remove declared basenames, not every credential or hidden file. Links/reparse
  points and unsafe paths are rejected. Source-drift/projection checks detect
  supported changes; they do not defend against an adversarial filesystem race.
- Timeouts apply to direct children. They are not a general descendant-process
  tree containment guarantee. Workspace cleanup preserves primary cancellation;
  it cannot prove arbitrary descendant processes have stopped.
- Native CONTROLLED Codex isolation is unavailable and fails closed. AMBIENT
  is explicitly uncontrolled/unfrozen and conditional on context stability
  across every trial and the whole reduction. No credential migration, linking,
  login, secret discovery or configuration bridge is provided.
- Global agent-readable filesystem isolation is NOT PROVEN. Repository scoped
  projection evidence concerns initial managed file presence before execution;
  agents/verifiers may read outside it or generate/change files afterward.
  External read isolation is not guaranteed, with no claim reads were observed.
- FAIL means the configured verifier/oracle returned its declared failure
  classification. Successful agent exit or natural-language statements are not
  a bug proof. Observed 1-minimality is relative to one input dimension and
  that run, with no global minimum or causal root-cause guarantee. M5 adds an
  empirical finite-horizon policy, with no confidence/iid guarantee. Trial
  budgets and agent launches do not bound backend model requests.
- Normal reports omit raw text, stdout/stderr, full argv, environment, private
  source anchors and exception messages. Relative names and digests can still
  expose project structure or low-entropy content. Explicit raw reduced exports
  contain requested text. Debug is deliberately sanitized and limited.
- Output protects declared inputs and source paths with per-file atomic
  no-clobber publication; explicit force permits replacement of a safe target.
  Unsupported hardlinks fail closed. Published complete output survives later
  stdout/temporary-cleanup failures. No multi-file/power-loss/adversarial-race
  transaction guarantee is made. Keep the source stable during execution.

This document describes boundaries; it grants no software license and invents
no security contact or private reporting channel. See [CLI](CLI.md) and
[report semantics](REPORTS.md). The human selected [MIT](../LICENSE) and
authorized three separate live trials with unknown backend request counts;
all three authorizations are consumed. The human authorized GitHub publication
of v0.1.0 under the scoped waiver. Additional model trials, PyPI publication or
deployment require new direct human approval.
