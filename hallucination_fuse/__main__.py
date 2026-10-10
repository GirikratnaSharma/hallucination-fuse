"""CLI: python -m hallucination_fuse <report.pdf> --context comms_publish [--human "..."]"""
from __future__ import annotations

import argparse
import json
import sys

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import audit, human, sources
from .fuse import APPROVE, BLOCK, ESCALATE, PULL_SOURCE, REWRITE, decide
from .parser import parse_report
from .rewrite import rewrite
from .trust import CONTEXTS, assess

con = Console()
COLOR = {APPROVE: "green", REWRITE: "yellow", PULL_SOURCE: "cyan", ESCALATE: "magenta", BLOCK: "red"}
CLS_COLOR = {"supported": "green", "partial": "yellow", "unverified": "cyan", "contradicted": "red"}


def bar(v):
    return "?" if v is None else "█" * v + "░" * (5 - v) + " %d/5" % v


def show_report(report, ta, ctx):
    con.rule("[bold]HALLUCINATION FUSE[/bold] · %s" % report.topic)
    con.print(Panel.fit(
        "[bold]Who relies on this:[/bold] %s\n[bold]For what:[/bold] %s\n[bold]Report:[/bold] %s\n[bold]Ezio verdict:[/bold] %s"
        % (ctx.owner, ctx.use, report.path, report.verdict or "(none parsed)"),
        title="Decision at stake"))

    t = Table(title="Claim evidence (verdict × source quality)", show_lines=False)
    for c in ("#", "Claim", "Ezio verdict", "Agent reads it as", "Sources", "Strength"):
        t.add_column(c)
    for a in ta.claims:
        srcs = ", ".join("%s%s" % (s.type, " " + s.date if s.date else "") for s in a.claim.sources) or "—"
        t.add_row(str(a.claim.id), a.claim.text[:70] + ("…" if len(a.claim.text) > 70 else ""), a.claim.verdict,
                  "[%s]%s[/]" % (CLS_COLOR[a.cls], a.cls.upper() + (" ★" if a.load_bearing else "")), srcs[:40], "%.2f" % a.strength)
    con.print(t)
    con.print("[dim]★ = load-bearing (numbers / forecasts / absolutes). UNVERIFIED = unknown, not false.[/dim]")

    s = Table(title="Module scores", show_header=False)
    for m, v in report.scores.items():
        s.add_row(m, bar(v))
    f = Table(title="Findings")
    f.add_column("Severity"); f.add_column("Kind"); f.add_column("Reasoning")
    for x in report.findings:
        f.add_row("[red]CRITICAL[/]" if x.severity == "CRITICAL" else x.severity, x.kind, x.text[:90])
    con.print(s, f)


def show_decision(d, title="Fuse decision"):
    c = COLOR[d.action]
    head = "[bold %s]%s[/]  %s" % (c, d.action, "⚡ FUSE TRIPPED" if d.tripped else "✓ fuse holds")
    gauge = "trust [%.2f ─── %.2f]  vs required %.2f   (blast radius %.2f)" % (d.trust_low, d.trust_high, d.required, d.blast)
    lines = [head, gauge, "", "[bold]Why (every reason traces to the report):[/bold]"]
    for r in d.reasons:
        lines.append("  [%s]→ %-8s[/] [bold]%s[/]: %s" % (COLOR[r.pushes], r.pushes, r.ref, r.text))
    if d.blind_spots:
        lines += ["", "[bold]What the report could NOT check:[/bold]"] + ["  • " + b for b in d.blind_spots]
    lines += ["", "[bold]Plan:[/bold]"] + ["  %d. %s" % (i + 1, p) for i, p in enumerate(d.plan)]
    con.print(Panel("\n".join(lines), title=title, border_style=c))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="hallucination_fuse")
    ap.add_argument("report")
    ap.add_argument("--context", default="comms_publish", choices=list(CONTEXTS))
    ap.add_argument("--human", action="append", help="scripted human response(s), in order")
    ap.add_argument("--no-human", action="store_true")
    ap.add_argument("--no-network", action="store_true")
    ap.add_argument("--json", action="store_true", help="print parsed report JSON and exit")
    a = ap.parse_args(argv)

    report = parse_report(a.report)
    if a.json:
        print(json.dumps(report.to_dict(), indent=2)); return
    ctx = CONTEXTS[a.context]
    ta = assess(report)
    show_report(report, ta, ctx)
    d = decide(report, ctx, ta)
    show_decision(d)
    audit.record("decision", {"report": a.report, "sha256": audit.file_sha256(a.report), "context": a.context,
                              "action": d.action, "trust": [d.trust_low, d.trust_high], "required": d.required,
                              "reasons": [r.__dict__ for r in d.reasons], "blind_spots": d.blind_spots})

    # PULL_SOURCE: go get independent evidence for unverified / weakly-sourced claims
    if d.action in (PULL_SOURCE, ESCALATE, BLOCK) and not a.no_network:
        ids = d.targets["unverified"] + d.targets["weak_supported"]
        by_id = {c.id: c for c in report.claims}
        for i in ids[:3]:
            hits = sources.lookup(by_id[i].text)
            con.print("[cyan]PULL_SOURCE[/] claim #%d → %s" % (i, "; ".join(h.get("title", h.get("error", "")) for h in hits) or "no leads"))
        if ids:
            con.print("[dim]Leads are rated 'Other' quality: they can inform the human, never auto-approve.[/dim]")

    if d.action == REWRITE:
        rw = rewrite(report, d)
        con.print(Panel(rw["text"][:1500], title="Proposed rewrite (%s) · removed %s · hedged %s" % (rw["engine"], rw["removed"], rw["hedged"]), border_style="yellow"))
        audit.record("rewrite", rw)

    if a.no_human:
        return
    script = list(a.human or [])

    def ask(q):
        con.print(Panel(q, title="🙋 Hand-off to %s" % ctx.owner, border_style="magenta"))
        if script:
            r = script.pop(0); con.print("[bold magenta]%s >[/] %s" % (ctx.owner, r)); return r
        if a.human is not None or not sys.stdin.isatty():
            return None
        return con.input("[bold magenta]%s >[/] " % ctx.owner)

    def on_update(resp, note, nd):
        con.print("[magenta]%s[/]" % note)
        audit.record("human", {"owner": ctx.owner, "response": resp, "note": note, "new_action": nd.action})
        show_decision(nd, title="Updated decision after %s's input" % ctx.owner)

    final = human.loop(report, d, ask, on_update)
    if final.action == REWRITE and d.action != REWRITE:
        rw = rewrite(report, final)
        con.print(Panel(rw["text"][:1500], title="Rewrite (%s)" % rw["engine"], border_style="yellow"))
    h = audit.record("final", {"report": a.report, "context": a.context, "action": final.action})
    ok, n, _ = audit.verify()
    con.print("[dim]Audit: %d entries, hash chain %s · head %s…[/dim]" % (n, "VALID" if ok else "BROKEN", h[:16]))


if __name__ == "__main__":
    main()
