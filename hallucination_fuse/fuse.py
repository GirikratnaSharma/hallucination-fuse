"""The fuse: decide the next action from evidence, with every reason traceable."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from .parser import TrustReport
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
    reasons: List[Reason] = []
    action = APPROVE

    contradicted = [a for a in ta.claims if a.cls == "contradicted"]
    detail_wrong = [a for a in ta.claims if a.cls == "contradicted_detail"]
    unverified = [a for a in ta.claims if a.cls == "unverified"]
    unv_opinion = [a for a in unverified if a.opinion]
    unv_partial = [a for a in unverified if not a.opinion and a.partial_aspects and any(k.status == "not_found" for k in a.claim.checks)]
    unv_fact = [a for a in unverified if a not in unv_opinion and a not in unv_partial]
    partial = [a for a in ta.claims if a.cls == "partial"]
    weak_supported = [a for a in ta.claims if a.cls == "supported" and a.strength < 0.5]
    strong_supported = [a for a in ta.claims if a.cls == "supported" and a.strength >= 0.5]
    tag = lambda a: '"%s"' % _short(a.claim.text, 70)

    # 1. Contradicted claims = negative evidence (never confused with "unverified").
    for a in contradicted:
        push = BLOCK if (a.load_bearing and blast >= 0.6) else REWRITE
        reasons.append(Reason("Claim %d" % a.claim.id, "%s — Contradicted: %s" % (tag(a), a.note), push))
        action = _max(action, push)
    for a in detail_wrong:
        strong = a.matched_type in ("Official", "Academic")
        push = REWRITE
        reasons.append(Reason("Claim %d" % a.claim.id, "%s — Contradicted (detail only): %s. Fixable by correcting the detail%s" % (
            tag(a), a.note, "" if strong else "; the contradicting source is '%s' quality, so correct + flag rather than block" % a.matched_type), push))
        action = _max(action, push)

    # 2. Unverified ≠ false. Three different kinds, three different actions.
    for a in unv_fact:
        push = PULL_SOURCE if (a.load_bearing or blast >= 0.5) else REWRITE
        reasons.append(Reason("Claim %d" % a.claim.id, "%s — Not enough evidence (%s). Unknown, not false → get evidence" % (tag(a), a.note), push))
        action = _max(action, push)
    for a in unv_partial:
        reasons.append(Reason("Claim %d" % a.claim.id, "%s — %s → keep the verified part, cut the rest" % (tag(a), a.note), REWRITE))
        action = _max(action, REWRITE)
    for a in unv_opinion:
        reasons.append(Reason("Claim %d" % a.claim.id, "%s — %s → attribute as opinion, don't state as fact" % (tag(a), a.note), REWRITE))
        action = _max(action, REWRITE)
    for a in partial:
        reasons.append(Reason("Claim %d" % a.claim.id, "%s — partially supported; needs hedging" % tag(a), REWRITE))
        action = _max(action, REWRITE)

    # 3. Supported claims: check WHICH source matched, not just the label.
    for a in weak_supported:
        push = PULL_SOURCE if blast >= 0.6 else APPROVE
        reasons.append(Reason("Claim %d" % a.claim.id, "Supported, but %s (strength %.2f)" % (a.note, a.strength), push))
        action = _max(action, push)
    for a in strong_supported:
        reasons.append(Reason("Claim %d" % a.claim.id, "Supported — %s" % a.note, APPROVE))

    # 4. Findings.
    for f in report.findings:
        if f.severity not in ("CRITICAL", "WARNING"):
            continue
        k = f.kind.lower()
        if k == "factual":
            push = ESCALATE if f.severity == "CRITICAL" else REWRITE
        else:
            push = REWRITE  # framing / sycophancy / tone are fixable in the text
        ev = (" Evidence: " + ", ".join('"%s"' % e for e in f.evidence[:2])) if f.evidence and k != "factual" else ""
        reasons.append(Reason("Finding: %s (%s)" % (f.title, f.severity), _short(f.text, 120) + ev, push))
        action = _max(action, push)

    # 5. Module scores (bars: full = good).
    for m, v in report.scores.items():
        if v is None:
            push = ESCALATE if (m == "Accuracy" and blast >= 0.6) else action
            reasons.append(Reason("Score: %s" % m, "Ezio could not score %s (%s): too few checkable claims to vouch for this answer" % (m, report.score_notes.get(m, "")), push))
            action = _max(action, push)
        elif v <= 2:
            push = REWRITE if m in ("Bias", "Tone", "Agreement") else ESCALATE
            reasons.append(Reason("Score: %s %d/5" % (m, v), "low — full bar means %s" % report.score_notes.get(m, "good"), push))
            action = _max(action, push)

    # 6. Trust coefficient vs. the bar for this blast radius.
    fixable = action == REWRITE and not contradicted and not unv_fact
    if ta.trust_low >= req and action in (APPROVE, REWRITE):
        reasons.append(Reason("Trust", "worst-case trust %.2f clears the %.2f bar for '%s'" % (ta.trust_low, req, ctx.use), action))
    elif fixable:
        reasons.append(Reason("Trust", "trust %.2f–%.2f is under the %.2f bar, but every issue is fixable in the text → rewrite, then re-verify with Ezio" % (ta.trust_low, ta.trust_high, req), REWRITE))
    elif ta.trust_high < req:
        push = BLOCK if (contradicted and blast >= 0.6) else ESCALATE
        reasons.append(Reason("Trust", "even best-case trust %.2f is below the %.2f bar" % (ta.trust_high, req), push))
        action = _max(action, push)
    elif action != APPROVE:
        reasons.append(Reason("Trust", "trust interval %.2f–%.2f straddles the %.2f bar: the answer hinges on what Ezio couldn't verify" % (ta.trust_low, ta.trust_high, req), action))
    elif action == APPROVE:
        reasons.append(Reason("Trust", "trust interval %.2f–%.2f straddles the %.2f bar; outcome depends on unverified claims" % (ta.trust_low, ta.trust_high, req), ESCALATE))
        action = ESCALATE

    # 7. Coverage: what the report never looked at.
    if ta.coverage < 0.6 and blast >= 0.6:
        reasons.append(Reason("Coverage", "only %.0f%% of fact-like sentences were checked" % (ta.coverage * 100), PULL_SOURCE))
        action = _max(action, PULL_SOURCE)

    contradicted = contradicted  # for plan
    unverified = unv_fact
    partial = partial + unv_partial + unv_opinion + detail_wrong
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
        steps.append("Rewrite claim(s) %s: correct wrong details, keep only verified parts, attribute opinions" % ", ".join("#%d" % a.claim.id for a in partial))
    fix_kinds = sorted({f.kind for f in ta.critical_findings if f.kind.lower() != "factual"})
    if fix_kinds:
        steps.append("Rewrite to neutralise: %s" % ", ".join(fix_kinds))
    if action == REWRITE:
        steps.append("Re-run the rewrite through Ezio; release only if the new report clears the bar")
    if action in (ESCALATE, BLOCK, REWRITE):
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
    return "%s: the fuse tripped (%s) because %s. Approve with justification, supply a source, request a rewrite, or reject?" % (
        ctx.owner,
        action,
        why,
    )
