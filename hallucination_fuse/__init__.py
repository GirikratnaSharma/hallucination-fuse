"""Hallucination Fuse — a circuit breaker for LLM outputs, driven by Ezio trust reports.

Two-line SDK:

    from hallucination_fuse import Fuse
    decision = Fuse("comms_publish").check("reports/wfh.pdf")   # .action, .reasons, .tripped

Or guard an action so it can only run when the fuse holds:

    @Fuse("client_note").guard(report="reports/ev.pdf")
    def send_note(): ...
"""
from __future__ import annotations

import functools

from . import audit
from .fuse import APPROVE, BLOCK, ESCALATE, PULL_SOURCE, REWRITE, Decision, decide
from .parser import TrustReport, parse_report
from .trust import CONTEXTS, Context, assess

__all__ = ["Fuse", "FuseTripped", "Decision", "CONTEXTS", "parse_report", "decide", "assess"]


class FuseTripped(RuntimeError):
    def __init__(self, decision: Decision):
        super().__init__(decision.summary())
        self.decision = decision


class Fuse:
    def __init__(self, context: str = "comms_publish", log: bool = True):
        if context not in CONTEXTS:
            raise ValueError("unknown context %r; choose from %s" % (context, list(CONTEXTS)))
        self.context = context
        self.log = log

    def check(self, report_path: str) -> Decision:
        report = parse_report(report_path)
        ta = assess(report)
        d = decide(report, self.context, ta)
        if self.log:
            audit.record(
                "decision",
                {
                    "report": report_path,
                    "report_sha256": audit.file_sha256(report_path),
                    "context": self.context,
                    "action": d.action,
                    "trust": [d.trust_low, d.trust_high],
                    "required": d.required,
                    "blast": d.blast,
                    "reasons": [r.__dict__ for r in d.reasons],
                    "blind_spots": d.blind_spots,
                },
            )
        return d

    def guard(self, report: str):
        def deco(fn):
            @functools.wraps(fn)
            def wrapper(*a, **kw):
                d = self.check(report)
                if d.tripped:
                    raise FuseTripped(d)
                return fn(*a, **kw)

            return wrapper

        return deco
