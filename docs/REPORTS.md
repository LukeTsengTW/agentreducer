# Report schema1

Every JSON reduction report has `schema_version`, `tool`, `domain`, `status`,
`exit_code`, `experiment`, `result`, `context`, `cost`, `limitations` and `error`.
Tool version is 0.1.0. Schema additions/incompatible changes require explicit
versioning decisions. Stable UTF-8 JSON uses LF and rejects nonfinite numbers.

`experiment` contains safe selected settings; it omits source/input/task/output
anchors, raw verifier/agent argv, environment and plaintext. It is not a full
replay recipe. Keep trusted inputs and argv separately. Normal human output is
a view of the same safe projection. Raw reduced artifacts are explicit opt-in.

Text results include original/reduced block, rendered character and UTF-8 byte
counts, rendered SHA-256 and retained original occurrence indices/digests.
Equal text occurrences remain distinct. Instruction heading ancestry includes
level/occurrence/title digest. Digests identify candidate representation, not
whole-experiment equivalence or privacy. Repository results contain snapshot
identity, managed/reducible/pinned byte/count statistics, `retained_paths`,
retained file digest/size/mode and separate `pinned_paths`. Their semantics are
initial fixed-file presence, not agent-generated output or a copied tree export.

`context.agent` carries mode/assurance/version/unknown-source descriptors and
the accepted M4 qualification, or null for command mode. AMBIENT is uncontrolled
and unfrozen: correctness depends on stable ambient context across reduction.
Global agent-readable filesystem isolation is NOT PROVEN. Native CONTROLLED
is unavailable; fake fixture behavior never certifies native isolation.

`context.stochastic` contains `policy`, exact fraction `{numerator,denominator}`,
budget, raw/committed counters, public qualification and available last-decision
metadata. Null last decision means unavailable, not an invented observation.
Observed finite-horizon 1-minimality is policy-relative; no confidence or iid
claim is implied. Unexecuted ERRORs remain unknown.

`context.repository_visibility` reports in-run scoped projection evidence and
external read isolation. Successful projection observations do not isolate the
global filesystem. Failure reports keep projection unverified; cumulative
completed counts do not prove whole-reduction visibility.

`cost` separates logical classifications, underlying trial attempts, agent and
verifier process launches, global trial budget, observed/committed/aborted
counts, projection attempts/completed checks, and monotonic elapsed seconds.
Each launch count is `{value,lower_bound,basis}`. Null value means unknown;
the lower bound preserves witnessed launches. Known zero is used only when
justified. Agent process count never establishes backend model request count.
Only elapsed time is normalized when comparing otherwise identical reports.

`limitations` is an array of `{code,message}` records. `error` is null or a fixed
`{code,stage,message}` envelope; library diagnostics and exception reprs are
omitted. Every non-success status has `result: null`. No formatter re-evaluates
an oracle or independently proves minimality. Retained FAIL and single-deletion
PASS witnesses belong to the existing run, as verified by offline E2E traces.

Output failures retain finite codes including `OUTPUT_EXISTS`,
`OUTPUT_PUBLISH_FAILED` and `OUTPUT_CLEANUP_FAILED`. The code
`OUTPUT_PUBLISHED_CLEANUP_FAILED` confirms a complete destination was published
and retained despite a subsequent temporary cleanup failure. Publication
failure diagnostics explain the default hardlink requirement without revealing
paths or exception messages. An already cancelled run remains `cancelled` with
exit 130 if its report publication fails; the secondary failure appears only
as a bounded delivery diagnostic on stderr.

Report output always ends with LF. Raw text export preserves the accepted
renderer exactly, without adding a final newline; repository path export adds
LF per retained reducible path and exports empty bytes for an empty set.
See [CLI publication rules](CLI.md) and [security boundaries](SECURITY.md).
