"""Human-in-the-loop: hand off to the accountable owner, then UPDATE the decision.

Human responses (interactive or scripted for reproducible demos):
  approve <justification>        override → APPROVE (justification is mandatory + audited)
  source <claim#> <type> <title> human supplies evidence → claim re-scored, decision recomputed
  falsify <claim#>               human knows the claim is wrong → treated as contradicted
  rewrite                        ask the agent for a rewrite
  reject <reason>                final BLOCK
"""
from __future__ import annotations

import shlex
from typing import Callable, Optional

from .fuse import APPROVE, BLOCK, REWRITE, Decision, decide
from .parser import Source, TrustReport


def apply_response(report: TrustReport, d: Decision, response: str):
    """Returns (new_decision, note, final). Mutates report claims for evidence updates."""
    parts = shlex.split(response.strip()) or ["?"]
    cmd = parts[0].lower()
    by_id = {c.id: c for c in report.claims}

    if cmd == "approve":
        just = " ".join(parts[1:]).strip()
        if len(just) < 10:
            return d, "Override refused: a justification of at least 10 characters is required.", False
        d.action, d.tripped = APPROVE, False
        d.plan = ["Released on human override by %s: %s" % (d.context.owner, just)]
        return d, "Human override recorded.", True

    if cmd == "reject":
        d.action, d.tripped = BLOCK, True
        d.plan = ["Blocked by %s: %s" % (d.context.owner, " ".join(parts[1:]) or "no reason given")]
        return d, "Human rejected.", True

    if cmd == "source" and len(parts) >= 4 and parts[1].isdigit() and int(parts[1]) in by_id:
        c = by_id[int(parts[1])]
        stype = parts[2].capitalize()
        c.sources.append(Source(type=stype if stype in ("Official", "Academic", "News", "Other") else "Human", title=" ".join(parts[3:]), date=None))
        if stype in ("Official", "Academic"):
            c.verdict = "Supported"
        new = decide(report, d.context)
        return new, "Claim %d re-scored with human-supplied %s source." % (c.id, stype), False

    if cmd == "falsify" and len(parts) >= 2 and parts[1].isdigit() and int(parts[1]) in by_id:
        by_id[int(parts[1])].verdict = "Contradicted"
        new = decide(report, d.context)
        return new, "Claim %s marked contradicted by human." % parts[1], False

    if cmd == "rewrite":
        d.action = REWRITE
        return d, "Rewrite requested.", True

    return d, "Unrecognised response. Use: approve <why> | source <#> <Official|Academic|News|Other> <title> | falsify <#> | rewrite | reject <why>", False


def loop(report: TrustReport, d: Decision, ask: Callable[[str], Optional[str]], on_update=None, max_turns: int = 6) -> Decision:
    for _ in range(max_turns):
        if d.handoff is None and d.action == APPROVE:
            return d
        q = d.handoff or "%s: decision is %s. Accept, or respond?" % (d.context.owner, d.action)
        resp = ask(q)
        if resp is None or resp.strip().lower() in ("", "accept", "ok"):
            return d
        d, note, final = apply_response(report, d, resp)
        if on_update:
            on_update(resp, note, d)
        if final:
            return d
    return d
