"""The fuse: decide the next action from evidence, with every reason traceable."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from .parser import TrustReport, validation_errors
from .trust import (
    CONTEXTS,
    Context,
    TrustAssessment,
    assess,
    blast_radius,
    required_trust,
)

APPROVE = "APPROVE"
REWRITE = "REWRITE"
PULL_SOURCE = "PULL_SOURCE"
ESCALATE = "ESCALATE"
BLOCK = "BLOCK"

SEVERITY_ORDER = [APPROVE, REWRITE, PULL_SOURCE, ESCALATE, BLOCK]


@dataclass
class Reason:
    ref: str  # e.g. "Claim 3", "Finding: Framing (CRITICAL)", "Score: Bias 2/5"
    text: str
    pushes: str  # which action this evidence pushes toward


@dataclass
class Decision:
    action: str
    tripped: bool
    trust_low: float
    trust_high: float
    required: float
    blast: float
    context: Context
    reasons: List[Reason]
    plan: List[str]
    blind_spots: List[str]
    handoff: Optional[str] = None  # question for the accountable human
    targets: dict = field(default_factory=dict)  # claim ids per bucket
    reliability: dict = field(default_factory=dict)
    report_sha256: Optional[str] = None

    def summary(self) -> str:
        return "%s | trust %.2f–%.2f vs required %.2f | blast %.2f" % (
            self.action,
            self.trust_low,
            self.trust_high,
            self.required,
            self.blast,
        )


def _max(a: str, b: str) -> str:
    return a if SEVERITY_ORDER.index(a) >= SEVERITY_ORDER.index(b) else b


def decide(report: TrustReport, context: str | Context, ta: Optional[TrustAssessment] = None) -> Decision:
    ctx = CONTEXTS[context] if isinstance(context, str) else context
    ta = ta or assess(report)
    blast = blast_radius(ctx, report)
    req = required_trust(blast)
    errors = validation_errors(report)
    if errors:
        return Decision(
            action=BLOCK, tripped=True, trust_low=0.0, trust_high=1.0,
            required=req, blast=blast, context=ctx,
            reasons=[Reason("Input validation", error, BLOCK) for error in errors],
            plan=["Supply a complete, parseable report before execution."],
            blind_spots=errors, handoff="%s: replace or repair the report; reliability is unknown." % ctx.owner,
            targets={"contradicted": [], "unverified": [], "partial": [], "weak_supported": []},
        )
    reasons: List[Reason] = []
    action = APPROVE
    from .metrics import evaluate
    reliability = evaluate(report).to_dict()

    contradicted = [a for a in ta.claims if a.cls == "contradicted"]
    unverified = [a for a in ta.claims if a.cls == "unverified"]
    partial = [a for a in ta.claims if a.cls == "partial"]
    weak_supported = [a for a in ta.claims if a.cls == "supported" and a.strength < 0.5]
    strong_supported = [a for a in ta.claims if a.cls == "supported" and a.strength >= 0.5]

    # 1. Contradicted claims: negative evidence. Load-bearing + high blast -> BLOCK.
    for a in contradicted:
        push = BLOCK if (a.load_bearing and blast >= 0.6) else REWRITE
        reasons.append(Reason("Claim %d" % a.claim.id, '"%s" — %s (%s)' % (_short(a.claim.text), a.claim.verdict, a.note), push))
        action = _max(action, push)

    # 2. Critical findings.
    for f in ta.critical_findings:
        k = f.kind.lower()
        if k in ("factual",):
            push = BLOCK if blast >= 0.6 else ESCALATE
        elif k in ("framing", "bias", "sycophancy", "tone", "sentiment"):
            push = REWRITE  # fixable by rewriting, facts may still stand
        else:
            push = ESCALATE
        reasons.append(Reason("Finding: %s (CRITICAL)" % f.kind, _short(f.text, 160), push))
        action = _max(action, push)

    # 3. Unverified claims: uncertainty, not falsehood -> get more evidence first.
    for a in unverified:
        push = PULL_SOURCE if a.load_bearing or blast >= 0.5 else REWRITE
        reasons.append(Reason("Claim %d" % a.claim.id, '"%s" — %s; treat as UNKNOWN, not false' % (_short(a.claim.text), a.claim.verdict), push))
        action = _max(action, push)

    for a in partial:
        reasons.append(Reason("Claim %d" % a.claim.id, '"%s" — partially supported; needs hedging' % _short(a.claim.text), REWRITE))
        action = _max(action, REWRITE)

    # 4. Source quality: "Supported" on weak sources is not the same as supported.
    for a in weak_supported:
        push = PULL_SOURCE if blast >= 0.6 or a.strength == 0 else APPROVE
        types = ", ".join(sorted({s.type for s in a.claim.sources})) or "no sources"
        reasons.append(Reason("Claim %d sources" % a.claim.id, "Supported, but only by %s (strength %.2f)" % (types, a.strength), push))
        action = _max(action, push)

    for a in strong_supported:
        reasons.append(Reason("Claim %d" % a.claim.id, "Supported — %s" % a.note, APPROVE))

    for a in ta.claims:
        for check in a.claim.checks:
            ref = "Claim %d / %s / Source %s" % (a.claim.id, check.aspect, check.source_idx or "unlinked")
            push = BLOCK if check.status == "contradicts" and blast >= .6 else (
                REWRITE if check.status in ("different", "contradicts") else
                APPROVE if check.status == "match" else PULL_SOURCE)
            if check.source_idx is not None and a.claim.source(check.source_idx) is None:
                push = PULL_SOURCE
            reasons.append(Reason(ref, check.detail, push))
            action = _max(action, push)

    if report.scores.get("Accuracy") is None:
        reasons.append(Reason("Score: Accuracy", "Accuracy was not scored; obtain evidence before execution.", PULL_SOURCE))
        action = _max(action, PULL_SOURCE)

    # 5. Module scores: low bars confirm what kind of fix is needed.
    for m, v in ta_scores(report).items():
        if v is None:
            continue
        if v <= 2:
            push = REWRITE if m in ("Bias", "Tone", "Agreement") else ESCALATE
            reasons.append(Reason("Score: %s %d/5" % (m, v), "low %s score" % m.lower(), push))
            action = _max(action, push)

    # 6. Trust coefficient vs. required trust for this blast radius.
    if ta.trust_low >= req and action in (APPROVE, REWRITE):
        reasons.append(Reason("Trust", "conservative evidence score %.2f clears the %.2f bar for '%s'" % (ta.trust_low, req, ctx.use), action))
    elif ta.trust_high < req:
        # Missing evidence requests research; it is not a factual contradiction.
        push = PULL_SOURCE if unverified else BLOCK if blast >= 0.6 else ESCALATE
        reasons.append(Reason("Trust", "even optimistic evidence score %.2f is below the %.2f bar" % (ta.trust_high, req), push))
        action = _max(action, push)
    elif action == APPROVE:
        # interval straddles the bar: the answer depends on the unknowns
        reasons.append(Reason("Trust", "heuristic score range %.2f–%.2f straddles the %.2f bar; outcome depends on unverified claims" % (ta.trust_low, ta.trust_high, req), ESCALATE))
        action = ESCALATE

    # 7. Coverage: what the report never looked at.
    if ta.coverage < 0.6 and blast >= 0.6:
        reasons.append(Reason("Coverage", "only %.0f%% of fact-like sentences were checked" % (ta.coverage * 100), PULL_SOURCE))
        action = _max(action, PULL_SOURCE)

    plan = _plan(action, contradicted, unverified, partial, weak_supported, ta, ctx)
    handoff = _handoff(action, ctx, contradicted, unverified, ta)
    return Decision(
        action=action,
        tripped=action != APPROVE,
        trust_low=ta.trust_low,
        trust_high=ta.trust_high,
        required=req,
        blast=blast,
        context=ctx,
        reasons=reasons,
        plan=plan,
        blind_spots=ta.blind_spots,
        handoff=handoff,
        targets={
            "contradicted": [a.claim.id for a in contradicted],
            "unverified": [a.claim.id for a in unverified],
            "partial": [a.claim.id for a in partial],
            "weak_supported": [a.claim.id for a in weak_supported],
        },
        reliability=reliability,
    )


def ta_scores(report: TrustReport) -> dict:
    return report.scores


def _short(t: str, n: int = 90) -> str:
    t = " ".join(t.split())
    return t if len(t) <= n else t[: n - 1] + "…"


def _plan(action, contradicted, unverified, partial, weak, ta, ctx) -> List[str]:
    steps = []
    if contradicted:
        steps.append("Remove or correct contradicted claim(s): %s" % ", ".join("#%d" % a.claim.id for a in contradicted))
    if unverified or weak:
        ids = [a.claim.id for a in unverified + weak]
        steps.append("Pull an independent Official/Academic source for claim(s): %s" % ", ".join("#%d" % i for i in ids))
    if partial:
        steps.append("Hedge partially-supported claim(s): %s" % ", ".join("#%d" % a.claim.id for a in partial))
    fix_kinds = sorted({f.kind for f in ta.critical_findings if f.kind.lower() != "factual"})
    if fix_kinds:
        steps.append("Rewrite to neutralise: %s" % ", ".join(fix_kinds))
    if action in (ESCALATE, BLOCK):
        steps.append("Hand off to %s for sign-off before anyone can '%s'" % (ctx.owner, ctx.use))
    if action == APPROVE:
        steps.append("Release to %s with the trust report attached" % ctx.owner)
    return steps


def _handoff(action, ctx, contradicted, unverified, ta) -> Optional[str]:
    if action not in (ESCALATE, BLOCK, PULL_SOURCE):
        return None
    bits = []
    if contradicted:
        bits.append("%d claim(s) are contradicted by evidence" % len(contradicted))
    if unverified:
        bits.append("%d claim(s) could not be verified" % len(unverified))
    if ta.critical_findings:
        bits.append("%d CRITICAL finding(s)" % len(ta.critical_findings))
    why = "; ".join(bits) or "trust is below the bar for this use"
    return "%s: the fuse tripped (%s) because %s. Submit a source for verification, request a rewrite, reject, or record an override request (execution remains held)?" % (
        ctx.owner,
        action,
        why,
    )
