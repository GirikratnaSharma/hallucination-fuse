"""Trust coefficient + blast radius.

Trust is computed from the evidence in the report, not the top-line verdict:

  * claims      — each claim is scored by verdict class AND source quality.
                  Unverified claims are *uncertainty*, contradicted claims are *negative
                  evidence*; the two are never conflated.
  * sources     — Official > Academic > News > Other, discounted when stale.
  * modules     — Accuracy / Bias / Tone / Agreement bars (filled blocks, 0-5).
  * findings    — CRITICAL findings are heavy penalties; NOTE findings are light.
  * coverage    — how much of the analyzed text was actually checked.

The output is an interval [trust_low, trust_high]:
  trust_low  = every unverified claim turns out false
  trust_high = every unverified claim turns out true
The width of the interval is the epistemic uncertainty the report leaves open.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional

from .parser import Claim, TrustReport

SOURCE_QUALITY = {"Official": 1.0, "Academic": 0.9, "News": 0.6, "Reference": 0.5, "Other": 0.3, "Human": 0.85}

SUPPORTED = {"Supported", "Mostly supported"}
PARTIAL = {"Partially supported"}
UNVERIFIED = {"Not enough evidence", "Insufficient evidence", "Unverified"}
CONTRADICTED = {"Disputed", "Contradicted", "Refuted", "Misleading", "False"}

# Modules where a full bar is good. Ezio bars are quality scores (5/5 = clean).
# Flip a module here if a report shows the opposite convention.
HIGHER_IS_BETTER = {"Accuracy": True, "Bias": True, "Tone": True, "Agreement": True}


OPINION = re.compile(r"\b(can|could|should|best|worst|removes?|ambiguity|fragment|outperforms?|clear winner|simple|serious|decisive|matters?|do not run|run on)\b", re.I)


def is_opinion(text: str) -> bool:
    """Normative / evaluative statements Ezio can't verify because they aren't facts."""
    return not re.search(r"\d", text) and bool(OPINION.search(text))


def matched_source(claim: Claim):
    """The source Ezio actually matched (or contradicted) the claim against — not just listed."""
    for k in claim.checks:
        if k.source_idx and k.status in ("match", "contradicts", "different"):
            return claim.source(k.source_idx), k
    return None, None


def verdict_class(v: str, detail: str = None) -> str:
    if v.startswith("Contradicted") and detail and "detail" in detail.lower():
        return "contradicted_detail"
    if v in SUPPORTED:
        return "supported"
    if v in PARTIAL:
        return "partial"
    if v in CONTRADICTED:
        return "contradicted"
    return "unverified"


def _year(d: Optional[str]) -> Optional[int]:
    if not d:
        return None
    m = re.search(r"(19|20)\d{2}", d)
    return int(m.group(0)) if m else None


def source_strength(claim: Claim, today: Optional[date] = None) -> float:
    """0..1: quality of the source Ezio matched, + diminishing credit for distinct corroborators."""
    today = today or date.today()
    if not claim.sources:
        return 0.0
    ms, _ = matched_source(claim)
    seen, uniq = set(), []
    for s in claim.sources:  # duplicates (same title listed 3x) are not corroboration
        key = s.title.lower()[:60]
        if key not in seen:
            seen.add(key)
            uniq.append(s)
    vals = []
    for s in ([ms] if ms else []) + [u for u in uniq if u is not ms]:
        q = SOURCE_QUALITY.get(s.type, 0.3)
        y = _year(s.date)
        if y is not None:
            age = today.year - y
            if age > 5:
                q *= 0.7
            elif age > 2:
                q *= 0.9
        vals.append(q)
    if not ms:
        vals.sort(reverse=True)
    # best source counts fully, corroborating sources add diminishing value
    s = vals[0] + sum(v * 0.25 / (i + 1) for i, v in enumerate(vals[1:]))
    return min(1.0, s)


@dataclass
class ClaimAssessment:
    claim: Claim
    cls: str
    strength: float
    load_bearing: bool
    note: str
    opinion: bool = False
    matched_type: Optional[str] = None
    partial_aspects: List[str] = field(default_factory=list)


@dataclass
class TrustAssessment:
    trust_low: float
    trust_high: float
    components: Dict[str, float]
    claims: List[ClaimAssessment]
    coverage: float
    unchecked_sentences: List[str]
    critical_findings: list
    note_findings: list
    blind_spots: List[str] = field(default_factory=list)

    @property
    def trust(self) -> float:
        return (self.trust_low + self.trust_high) / 2

    @property
    def uncertainty(self) -> float:
        return self.trust_high - self.trust_low


_FACTY = re.compile(r"(\d|percent|%|\bmost\b|\bmajority\b|\bstud(y|ies)\b|\breport|\bwill\b|\bby 20\d\d)", re.I)


def _sentences(text: str) -> List[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", " ".join(text.split())) if len(s.strip()) > 25]


def _overlap(a: str, b: str) -> float:
    wa = set(re.findall(r"[a-z0-9]{4,}", a.lower()))
    wb = set(re.findall(r"[a-z0-9]{4,}", b.lower()))
    return len(wa & wb) / max(1, min(len(wa), len(wb)))


def assess(report: TrustReport, today: Optional[date] = None) -> TrustAssessment:
    # ---- claims ----
    cas: List[ClaimAssessment] = []
    for c in report.claims:
        cls = verdict_class(c.verdict, c.verdict_detail)
        st = source_strength(c, today)
        load = bool(re.search(r"\d|%|percent|most|majority|every|always|never|millions", c.text, re.I))
        ms, chk = matched_source(c)
        mtype = ms.type if ms else None
        mdesc = ("Source %d %s '%s'" % (ms.idx, ms.type, ms.title[:50])) if ms else "no matching source"
        partial = [k.aspect for k in c.checks if k.status == "match"]
        op = is_opinion(c.text)
        if cls == "supported":
            note = "matched by %s" % mdesc
            if mtype and mtype not in ("Official", "Academic"):
                offs = [s for s in c.sources if s.type == "Official"]
                if offs:
                    note += " — Official source listed but NOT the one that matched"
        elif cls == "contradicted_detail":
            note = "a detail is contradicted by %s" % mdesc
        elif cls == "contradicted":
            note = "contradicted by %s" % mdesc
        elif cls == "partial":
            note = "only partially supported"
        else:
            if partial and any(k.status == "not_found" for k in c.checks):
                note = "only the %s matched (%s); the rest was not found" % ("/".join(partial), mdesc)
            elif chk and chk.status == "match":
                note = "matched only %s — too weak to verify" % mdesc
            elif chk and chk.status == "different":
                note = "%s gives a different detail" % mdesc
            else:
                note = "not found in %d source(s) checked" % len(c.sources)
            if op:
                note += "; reads as opinion, not a checkable fact"
        cas.append(ClaimAssessment(c, cls, st, load, note, op, mtype, partial))

    def claim_score(optimistic: bool) -> float:
        if not cas:
            return 0.5  # no claims checked = no evidence either way
        tot, w = 0.0, 0.0
        for a in cas:
            weight = 1.5 if a.load_bearing else 1.0
            if a.cls == "supported":
                v = 0.55 + 0.45 * a.strength
            elif a.cls == "partial":
                v = 0.5
            elif a.cls == "contradicted":
                v = 0.0
            elif a.cls == "contradicted_detail":
                v = 0.25
            else:
                v = 0.85 if optimistic else 0.15
            tot += v * weight
            w += weight
        return tot / w

    # ---- modules ----
    mod_vals = []
    for m, v in report.scores.items():
        if v is None:
            continue
        x = v / 5.0
        mod_vals.append(x if HIGHER_IS_BETTER.get(m, True) else 1 - x)
    module_score = sum(mod_vals) / len(mod_vals) if mod_vals else 0.5

    # ---- findings ----
    crit = [f for f in report.findings if f.severity == "CRITICAL"]
    benign = re.compile(r"no significant|neutral and objective|reads as independent", re.I)
    notes = [f for f in report.findings if f.severity in ("NOTE", "WARNING") and not benign.search(f.text)]
    warn = [f for f in notes if f.severity == "WARNING"]
    findings_score = max(0.0, 1.0 - 0.3 * len(crit) - 0.15 * len(warn) - 0.05 * (len(notes) - len(warn)))

    # ---- coverage: which fact-like sentences in the answer were never checked ----
    sents = [s for s in _sentences(report.analyzed_text) if _FACTY.search(s)]
    unchecked = [s for s in sents if not any(_overlap(s, a.claim.text) > 0.4 for a in cas)]
    coverage = 1.0 - (len(unchecked) / len(sents)) if sents else (1.0 if cas else 0.0)

    def combine(cs: float) -> float:
        return 0.45 * cs + 0.25 * module_score + 0.20 * findings_score + 0.10 * coverage

    lo, hi = combine(claim_score(False)), combine(claim_score(True))

    blind = []
    n_unv = sum(1 for a in cas if a.cls == "unverified")
    if n_unv:
        blind.append("%d of %d checked claims could not be verified" % (n_unv, len(cas)))
    if unchecked:
        blind.append("%d fact-like sentence(s) in the answer were never checked as claims" % len(unchecked))
    weak = [a for a in cas if a.cls == "supported" and a.strength < 0.5]
    newsy = [a for a in cas if a.cls == "supported" and a.matched_type not in (None, "Official", "Academic")]
    if newsy:
        blind.append("Supported claim(s) %s matched a %s source, not an Official one" % (", ".join("#%d" % a.claim.id for a in newsy), "/".join(sorted({a.matched_type for a in newsy}))))
    if weak:
        blind.append("%d 'Supported' claim(s) rest only on low-quality (Other/News) or stale sources" % len(weak))
    for m, v in report.scores.items():
        if v is None:
            blind.append("Ezio could not score %s (%s)" % (m, report.score_notes.get(m, "missing")))
    ops = [a for a in cas if a.cls == "unverified" and a.opinion]
    if ops:
        blind.append("%d 'unverified' claim(s) are opinions — no source can verify them" % len(ops))

    return TrustAssessment(
        trust_low=round(lo, 3),
        trust_high=round(hi, 3),
        components={
            "claims_pessimistic": round(claim_score(False), 3),
            "claims_optimistic": round(claim_score(True), 3),
            "modules": round(module_score, 3),
            "findings": round(findings_score, 3),
            "coverage": round(coverage, 3),
        },
        claims=cas,
        coverage=round(coverage, 3),
        unchecked_sentences=unchecked,
        critical_findings=crit,
        note_findings=notes,
        blind_spots=blind,
    )


# ---------------- Blast radius ----------------

@dataclass
class Context:
    """Who is relying on the answer, and for what."""

    key: str
    owner: str  # accountable human
    use: str
    reach: float  # 0 internal .. 1 public
    irreversibility: float  # 0 easily retracted .. 1 permanent
    stakes: float  # 0 trivia .. 1 money/safety/legal


CONTEXTS: Dict[str, Context] = {
    "comms_publish": Context(
        "comms_publish", "Comms Lead", "publish as a public-facing article", 0.9, 0.8, 0.6
    ),
    "client_note": Context(
        "client_note", "Senior Analyst", "include in a paid client research note", 0.6, 0.7, 0.9
    ),
    "policy_brief": Context(
        "policy_brief", "Policy Director", "brief a legislator / regulator", 0.7, 0.7, 0.9
    ),
    "internal_memo": Context(
        "internal_memo", "Team Lead", "share in an internal discussion memo", 0.2, 0.2, 0.3
    ),
}


def blast_radius(ctx: Context, report: TrustReport) -> float:
    base = 0.4 * ctx.reach + 0.3 * ctx.irreversibility + 0.3 * ctx.stakes
    # content amplifiers: forecasts, money, health/safety language raise the stakes
    t = report.analyzed_text.lower()
    amp = 0.0
    if re.search(r"\bwill\b|by 20\d\d|forecast|predict", t):
        amp += 0.05
    if re.search(r"invest|market share|revenue|billion|\$", t):
        amp += 0.05
    if re.search(r"safety|radiation|death|health|accident", t):
        amp += 0.05
    return round(min(1.0, base + amp), 3)


def required_trust(blast: float) -> float:
    """The higher the blast radius, the more trust we demand before execution."""
    return round(0.45 + 0.4 * blast, 3)
