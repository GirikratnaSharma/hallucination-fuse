# ⚡ Hallucination Fuse — a Trust Agent for Ezio reports

A real-time **circuit breaker** for LLM output. It takes an [Ezio](https://ezioai.io) trust report as input, weighs the *evidence* (claims, source quality, module scores, findings, coverage) and decides the next action before anything high-stakes executes:

| Action | When |
|---|---|
| `APPROVE` | worst-case trust clears the bar for this use |
| `REWRITE` | facts hold, but framing / tone / sycophancy / partial claims need fixing |
| `PULL_SOURCE` | load-bearing claims are **unverified** (unknown ≠ false) → go get independent evidence |
| `ESCALATE` | the trust interval straddles the bar → an accountable human decides |
| `BLOCK` | **contradicted** load-bearing claims or critical factual findings at high blast radius |

Built for the Ezio *Build a Trust Agent* buildathon ([challenge](https://github.com/ezioai/Build-Trust-Agent)).

## How it decides

1. **Parse** the PDF (pypdf) → analyzed text, verdict, module bars (filled `█` blocks counted), claims + verdicts + sources, findings.
2. **Trust coefficient as an interval** `[trust_low, trust_high]`
   - `trust_low` = every unverified claim turns out false, `trust_high` = every unverified claim turns out true.
   - Width = the uncertainty the report leaves open. Contradicted claims score 0 in both, so they are never confused with "unverified".
   - Claims weighted by **source quality** (Official 1.0 > Academic 0.9 > News 0.6 > Other 0.3, discounted when stale) and whether they're load-bearing (numbers, forecasts, absolutes).
   - Plus module scores, CRITICAL/NOTE findings and **coverage** (fact-like sentences that Ezio never checked).
3. **Blast radius** from *who relies on the answer and for what* (`--context`): reach × irreversibility × stakes, amplified by forecasts / money / safety language.
   Required trust = `0.45 + 0.4 × blast`.
4. **Fuse policy**: every reason points to a specific claim, score, finding or source. It also lists what the report could not check.
5. **Human in the loop**: on ESCALATE / BLOCK / PULL_SOURCE it hands off to the accountable owner (Comms Lead, Senior Analyst, …). The owner can `approve <justification>`, `source <#> <type> <title>`, `falsify <#>`, `rewrite` or `reject <why>`, and **the agent re-scores and updates its decision**.
6. **Immutable audit**: every decision, rewrite and human response goes to a SHA-256 hash-chained JSONL (`audit/fuse_audit.jsonl`), verified at the end of every run.

## Quickstart

```bash
pip install -r requirements.txt
python -m hallucination_fuse reports/wfh.pdf --context comms_publish       # interactive human hand-off
python -m hallucination_fuse reports/ev.pdf  --context client_note \
    --human "source 2 Official IEA Global EV Outlook 2025"                  # scripted human (for demos)
python -m hallucination_fuse tests/fixtures/synthetic_wfh.txt              # synthetic fixture, no PDF needed
```

Contexts: `comms_publish`, `client_note`, `policy_brief`, `internal_memo`.
Set `ANTHROPIC_API_KEY` to use Claude for rewrites; otherwise a rule-based rewriter is used.

## Two-line SDK

```python
from hallucination_fuse import Fuse
decision = Fuse("client_note").check("reports/ev.pdf")   # .action .reasons .blind_spots .tripped
```

Or guard the risky action itself:

```python
@Fuse("comms_publish").guard(report="reports/wfh.pdf")
def publish(): ...        # raises FuseTripped instead of running if trust is too low
```

## Team
- Girikratna Sharma
- Merve Uçmaz
