# Hallucination Fuse

A local trust-report prototype that checks evidence before a text-publishing
function can run. Built for the Ezio Build a Trust Agent challenge.

The local project incorporates upstream commit `795ca23`, including the real
nuclear and work-from-home PDF reports, plus reliability metrics and guard fixes.

## Run

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m hallucination_fuse.demo
.venv/bin/python -m unittest discover -s tests -v
```

The offline demo runs both real PDFs through a guarded mock publication. It
writes detailed JSON to `output/demo/` and audit records to `audit/`. No external
publication occurs. Current public-comms decisions:

- Nuclear: `REWRITE`, correcting an explicitly detail-only contradiction and qualifying unsupported parts.
- Work from home: `ESCALATE`, with unresolved claims and unscored Accuracy.

Both decisions stop execution. Outcomes follow evidence and context, not filenames.

## CLI and metrics

```sh
.venv/bin/python -m hallucination_fuse reports/nuclear.pdf --context comms_publish --no-network --human rewrite
.venv/bin/python -m hallucination_fuse reports/wfh.pdf --context comms_publish --no-network --no-human
.venv/bin/python -m hallucination_fuse.metrics reports/wfh.pdf
```

An optional Claude rewrite requires installing `anthropic` and configuring a
supported model with `FUSE_MODEL` plus `ANTHROPIC_API_KEY`. Otherwise rewrites use
local rules. Live LLM calls are not part of the offline validation.

## Guard an action

```python
from hallucination_fuse import Fuse

@Fuse("comms_publish").guard(report="reports/wfh.pdf")
def publish(payload: str):
    # Call a fixed destination here only after approval.
    return publisher.send(payload)
```

The decorated function must accept exactly one text argument, named `payload`
(or set `payload_arg`). The guard checks a single snapshot of the report and
requires the payload to match its analyzed text, ignoring whitespace differences.
It raises `FuseTripped` for every held decision. Changed payloads require a new
report. Other parameters and async callables are rejected rather than left
unbound. The callable's destination and policy context are the integrator's
responsibility; this prototype does not sandbox arbitrary functions.

`Fuse("comms_publish").check(path)` provides a decision without executing anything.

## Evidence and review

- Empty, incomplete, or structurally invalid reports block execution.
- Explicit `Not scored` is preserved as unavailable; unscored Accuracy requests evidence.
- Sub-check contradictions, missing details, and unresolved source indices can
  reduce support. A matching sub-check cannot upgrade an unverified claim.
- Missing evidence requests research instead of being treated as a contradiction.
- Source metadata is a proxy, not proof of source authenticity or relevance.
- Submitted source titles are stored as pending evidence and cannot raise trust.
- `rewrite` returns a proposed revision, keeps execution held, and requires a new report.
- `approve <reason>` records an override request but cannot release execution;
  authenticated, payload-bound reviewer authorization is not implemented.
- `reject` blocks. `falsify` marks a claim contradicted and recomputes the decision.

Trust ranges and reliability metrics are explainable heuristic scores, not model
internal confidence or calibrated probabilities. See [METRICS.md](METRICS.md).

## Audit guarantees

Logs are hash-linked, with serialized writers and corruption checks before append.
They record decisions, review responses, revisions, and guarded execution outcomes.
They are **not immutable** and do not authenticate the reviewer or report issuer.
Use `audit.verify(path, expected_head=checkpoint)` with an independently retained
checkpoint to detect tail deletion or full replacement. Internal verification alone
cannot establish historical completeness. File locking currently requires POSIX.
A post-execution logging failure cannot undo an external side effect.

## Team

- Girikratna Sharma
- Merve Uçmaz
