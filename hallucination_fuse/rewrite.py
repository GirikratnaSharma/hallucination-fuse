"""REWRITE action: produce a safer version of the analyzed text, driven by the evidence."""
from __future__ import annotations

import re
from typing import List

from . import llm
from .fuse import Decision
from .parser import TrustReport
from .trust import _overlap, _sentences

SYSTEM = (
    "You are a careful editor working inside a trust pipeline. Rewrite the text so it only "
    "asserts what the evidence supports. Remove contradicted claims, hedge or attribute "
    "unverified claims ('some reports suggest…', 'could not be independently verified'), "
    "neutralise framing bias and sycophancy, and keep everything else. Return only the rewritten text."
)


def rewrite(report: TrustReport, d: Decision) -> dict:
    by_id = {c.id: c for c in report.claims}
    remove = [by_id[i] for i in d.targets.get("contradicted", []) if i in by_id]
    hedge = [by_id[i] for i in d.targets.get("unverified", []) + d.targets.get("partial", []) if i in by_id]
    style = [r for r in d.reasons if r.ref.startswith("Finding") and r.pushes == "REWRITE"]

    prompt = "TEXT:\n%s\n\nREMOVE (contradicted):\n%s\n\nHEDGE (unverified/partial):\n%s\n\nSTYLE ISSUES:\n%s" % (
        report.analyzed_text,
        "\n".join("- " + c.text for c in remove) or "none",
        "\n".join("- " + c.text for c in hedge) or "none",
        "\n".join("- " + r.text for r in style) or "none",
    )
    out = llm.ask(SYSTEM, prompt)
    if out:
        return {"engine": "claude", "text": out, "removed": [c.id for c in remove], "hedged": [c.id for c in hedge]}

    # Deterministic fallback: sentence-level surgery driven by the report's own evidence.
    syc = [e for f in report.findings if f.kind == "Sycophancy" for e in f.evidence]
    text = report.analyzed_text
    for e in syc:  # strip the exact flattery phrases Ezio flagged
        if e.endswith("."):
            text = text.replace(e, "")
        else:
            text = re.sub(re.escape(e), "a strong option", text, flags=re.I)
    corrections = {}
    for c in report.claims:
        for k in c.checks:
            if k.status == "contradicts":
                m = re.search(r"sources say (.+?)\s*\(Source", k.detail)
                if m:
                    corrections[c.id] = m.group(1).strip().rstrip(".")
    kept: List[str] = []
    for s in _sentences(text):
        fix = next((corrections[c.id] for c in report.claims if c.id in corrections and _overlap(s, c.text) > 0.5), None)
        if fix:
            kept.append(re.sub(r"[.!?]$", "", s) + "; however, sources note: " + fix[0].lower() + fix[1:] + ".")
            continue
        if any(_overlap(s, c.text) > 0.5 for c in remove):
            continue
        if any(_overlap(s, c.text) > 0.5 for c in hedge):
            s = re.sub(r"[.!?]$", "", s) + " (this could not be independently verified)."
        if style:
            if re.search(r"anyone who disagrees|out of touch|only a fool|obviously wrong|^not someday|^now\.?$", s, re.I):
                continue  # dismissive framing flagged by the report
            s = re.sub(r"\b(clearly|obviously|undeniably|simply|certainly)\s+", "", s, flags=re.I)
        kept.append(s[:1].upper() + s[1:])
    if style:
        kept.append("Trade-offs and alternative views (cost, timelines, risks, and competing options) should be weighed alongside these points.")
    return {"engine": "rules", "text": " ".join(kept), "removed": [c.id for c in remove], "hedged": [c.id for c in hedge]}
