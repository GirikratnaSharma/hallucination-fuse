"""Explainable report metrics, not calibrated probabilities or model confidence.

Run: python -m hallucination_fuse.metrics report.pdf
Weights identify claims essential to the proposed action; default weight is 1.
"""
from __future__ import annotations

import argparse
import json
import math
import re
from dataclasses import asdict, dataclass
from typing import Mapping

from .parser import TrustReport, parse_report, validation_errors
from .trust import claim_class


@dataclass(frozen=True)
class Metric:
    value: float | None
    explanation: str
    claim_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class ReliabilityMetrics:
    metrics: dict[str, Metric]
    holds: tuple[str, ...]
    limitations: tuple[str, ...]

    def to_dict(self):
        return asdict(self)


def _normalize(text: str) -> str:
    return " ".join(re.findall(r"\w+", text.casefold()))


def evaluate(report: TrustReport, weights: Mapping[int, float] | None = None) -> ReliabilityMetrics:
    """All values are in [0, 1]; None means not measurable.

    Contradiction and uncertainty are adverse rates. Other metrics increase
    with stronger evidence. Holds are diagnostics, never execution approval.
    """
    claims = report.claims
    ids = [c.id for c in claims]
    if len(ids) != len(set(ids)):
        raise ValueError("Claim IDs must be unique")
    weights = dict(weights or {})
    if set(weights) - set(ids):
        raise ValueError("Weights reference unknown claim IDs")
    if any(not math.isfinite(w) or w <= 0 for w in weights.values()):
        raise ValueError("Weights must be finite and positive")
    w = {c.id: weights.get(c.id, 1.0) for c in claims}
    total = sum(w.values())
    classes = {c.id: claim_class(c) for c in claims}
    metrics = {}

    def rate(name, categories, explanation):
        selected = tuple(c.id for c in claims if classes[c.id] in categories)
        value = sum(w[i] for i in selected) / total if total else None
        metrics[name] = Metric(value, explanation, selected)

    rate("supported_share", {"supported"}, "Weighted share supported after conservative sub-check reconciliation; no independent verification.")
    rate("partial_share", {"partial"}, "Weighted share with only partial support.")
    rate("uncertainty_share", {"unverified"}, "Weighted share without a conclusive verdict, including unrecognized verdicts.")
    rate("contradiction_share", {"contradicted", "contradicted_detail"}, "Weighted share carrying an adverse verdict; never averaged away by the support score.")

    # A provenance proxy only: repeated citations do not increase this score.
    quality = {"official": 1.0, "academic": 0.9, "reference": 0.6, "news": 0.6, "other": 0.3}
    q = {c.id: max((quality.get(s.type.casefold(), 0.0)
                    for s in c.sources if s.title.strip()), default=0.0) for c in claims}
    metrics["source_metadata_quality"] = Metric(
        sum(w[i] * q[i] for i in ids) / total if total else None,
        "Weighted best source-type score per claim: Official 1, Academic .9, News/Reference .6, Other .3. Missing/unrecognized sources score 0.", tuple(ids))

    subchecks = [(c, check) for c in claims for check in c.checks]
    linked = [(c, check) for c, check in subchecks if check.source_idx is not None and c.source(check.source_idx) is not None]
    metrics["subcheck_match_share"] = Metric(
        sum(check.status == "match" for _, check in subchecks) / len(subchecks) if subchecks else None,
        "Fraction of parsed sub-checks labeled match by Ezio; not independent verification.")
    metrics["subcheck_source_linkage"] = Metric(
        len(linked) / len(subchecks) if subchecks else None,
        "Fraction of sub-checks whose source index resolves to a source within the claim.")

    # Conservative text alignment, not a semantic coverage claim.
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", report.analyzed_text.strip()) if _normalize(s)]
    claim_texts = {_normalize(c.text) for c in claims if _normalize(c.text)}
    covered = sum(_normalize(s) in claim_texts for s in sentences)
    metrics["exact_sentence_coverage"] = Metric(
        covered / len(sentences) if sentences else None,
        f"{covered}/{len(sentences)} analyzed-text sentences exactly align with a checked claim after case/punctuation normalization. Paraphrases may be missed.")

    valid_scores = sum((type(report.scores.get(k)) is int and 0 <= report.scores[k] <= 5)
                       or (report.scores.get(k) is None and report.score_notes.get(k, "").casefold() == "not scored")
                       for k in ("Accuracy", "Bias", "Tone", "Agreement"))
    checks = [bool(report.analyzed_text.strip()), bool(claims) and all(c.text.strip() for c in claims),
              bool(report.verdict.strip()), valid_scores == 4,
              bool(report.findings) and bool(re.search(r"(?im)^\s*Findings\s*:?\s*$", report.raw_text))]
    metrics["input_completeness"] = Metric(sum(checks) / len(checks),
        "Presence checks for analyzed text, nonempty claims, verdict, four module entries (including explicit Not scored), and parsed findings; not parser correctness.")

    coverage = metrics["exact_sentence_coverage"].value
    support_value = {"supported": 1.0, "partial": 0.5, "unverified": 0.0, "contradicted": 0.0, "contradicted_detail": 0.0}
    evidence = sum(w[i] * q[i] * support_value[classes[i]] for i in ids) / total if total else None
    metrics["evidence_readiness"] = Metric(
        evidence * coverage if evidence is not None and coverage is not None and not validation_errors(report) else None,
        "Heuristic: weighted support credit × source-type quality, multiplied by exact sentence coverage. Requires all completeness checks. Not a probability or permission to execute.", tuple(ids))

    holds = []
    if validation_errors(report):
        holds.append("Incomplete input: do not authorize execution from these metrics.")
    if any(classes[i] in ("contradicted", "contradicted_detail") for i in ids):
        holds.append("Adverse claim verdict requires explicit review regardless of the aggregate score.")
    if any(classes[i] == "unverified" for i in ids):
        holds.append("Unresolved claims require evidence or a documented action-specific decision.")
    if any(q[i] == 0 for i in ids):
        holds.append("One or more claims lack usable source metadata.")
    if coverage is None or coverage < 1:
        holds.append("Coverage is incomplete or unknown; inspect unmatched text before execution.")
    return ReliabilityMetrics(metrics, tuple(holds), (
        "These metrics trust the parsed report; they do not retrieve or verify source content.",
        "Source labels, dates, independence, and report provenance are not authenticated.",
        "Sentence coverage is a conservative text-match estimate, not semantic or factual coverage.",
        "Weights and source scores are policy choices; no statistical calibration is claimed.",
        "Blast radius and payload-to-report binding must be enforced by the execution policy.",
    ))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report")
    args = parser.parse_args()
    print(json.dumps(evaluate(parse_report(args.report)).to_dict(), indent=2))


if __name__ == "__main__":
    main()
