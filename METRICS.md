# Reliability metrics

The metrics are attached to each valid decision and shown in the CLI. The
execution policy separately enforces report validation, sub-check evidence,
context, and payload binding; metric scores alone never grant permission.

Run from this directory:

```sh
python3 -m hallucination_fuse.metrics tests/fixtures/synthetic_wfh.txt
python3 -m unittest discover -s tests -v
```

For PDFs, install the repository dependencies and substitute the PDF path.
Regression checks now include both real Ezio PDFs plus synthetic edge cases.

```python
from hallucination_fuse.metrics import evaluate
from hallucination_fuse.parser import parse_report

report = parse_report("reports/wfh.pdf")
result = evaluate(report)  # equal claim weights
print(result.to_dict())
# Optionally identify claims important to the proposed action:
# result = evaluate(report, weights={1: 3.0})
```

All metrics range from 0 to 1. Null means unknown, never zero uncertainty.

| Metric | Meaning |
|---|---|
| Supported share | Weighted fraction of claims reported supported |
| Partial share | Weighted fraction reported partially supported |
| Uncertainty share | Weighted fraction unverified or carrying an unknown verdict |
| Contradiction share | Weighted fraction with an adverse verdict |
| Source metadata quality | Weighted best source-type score per claim; duplicates add nothing |
| Exact sentence coverage | Fraction of analyzed-text sentences matching checked claims |
| Input completeness | Presence of five expected input elements, including explicit Not scored values |
| Sub-check match share | Fraction of detailed checks marked match |
| Sub-check source linkage | Fraction of detailed checks linked to a parsed source |
| Evidence readiness | Source-adjusted support multiplied by exact coverage |

Evidence readiness gives support credit 1 for supported, .5 for partial, and 0
for unknown or contradicted claims. Source-type scores are Official 1, Academic
.9, News/Reference .6, Other .3, and missing/unrecognized 0. These are transparent policy
choices requiring validation, not measured probabilities. Readiness is null
unless all completeness checks pass. A perfect metadata score does not establish
that a source exists, is independent, or actually supports the claim.

The synthetic fixture yields 33.3% supported, 33.3% uncertain, 33.3% adverse,
60% exact coverage, and .20 readiness. Review holds remain visible regardless
of the composite score. Holds are diagnostic output; the caller must enforce
them. No holds is not an execution authorization.

Coverage deliberately uses exact normalized sentence matching. It can miss
paraphrases and counts nonfactual sentences, so it must not be presented as
semantic coverage. Completeness is not proof that parsing was correct.

Further validation: visually compare extracted evidence with the PDFs, label
action-essential claims, and test the metrics
against human judgments before choosing execution thresholds. Keep blast
radius separate from evidence reliability and bind approval to the payload.
