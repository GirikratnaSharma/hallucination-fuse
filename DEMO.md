# Demo script (1–2 min)

**Main report: Work from home (`reports/wfh.pdf`).** A Comms Lead wants to publish this as a public company post.

```bash
python3 -m hallucination_fuse reports/wfh.pdf --context comms_publish --no-network \
  --human "source 3 Official Stanford WFH Research: hybrid commuting survey 2025" --human "rewrite"
```
What to point at:
- The claim table. 4 of 5 claims are unverified, but the agent splits them: 3 are opinions (rewrite them as opinion) and 1 is a fact (pull a source).
- Claim 5 is Supported and matched an Official source.
- Ezio could not score Accuracy, the stakes are public, and the framing finding is CRITICAL, so the decision is **ESCALATE** to the Comms Lead.
- The human supplies a source and the agent re-scores claim 3. It still holds because accuracy can't be scored. The human asks for a rewrite.
- The audit hash chain is VALID.

**Second report: Nuclear energy (`reports/nuclear.pdf`).** A Policy Director wants to brief a legislator.

```bash
python3 -m hallucination_fuse reports/nuclear.pdf --context policy_brief --no-network \
  --human "approve Rewrite reviewed; weather caveat and verified figures retained"
```
What to point at:
- Claim 1 is "Contradicted: detail" and the contradicting source is Other quality, so the agent corrects the detail rather than blocking.
- Claim 2: only the "60–80 years" amount matched an Official source, so the agent keeps that part and cuts the rest.
- Claim 4 is Supported, but it matched a News source, not the listed Official IEA source. The agent flags this.
- Sycophancy is CRITICAL ("Absolutely.", "the clear winner"), and those phrases are stripped in the rewrite.
- Every issue is fixable in the text, so the decision is **REWRITE**, then re-verify with Ezio, then the Policy Director signs off.
