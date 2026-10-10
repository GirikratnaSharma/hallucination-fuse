"""Hallucination Fuse — a circuit breaker for LLM outputs, driven by Ezio trust reports.

Two-line SDK:

    from hallucination_fuse import Fuse
    decision = Fuse("comms_publish").check("reports/wfh.pdf")   # .action, .reasons, .tripped

Or guard an action so it can only run when the fuse holds:

    @Fuse("client_note").guard(report="reports/ev.pdf")
    def send_note(payload): ...
"""
from __future__ import annotations

import functools
import hashlib
import inspect
from io import BytesIO
from pathlib import Path
from dataclasses import replace

from . import audit
from .fuse import APPROVE, BLOCK, ESCALATE, PULL_SOURCE, REWRITE, Decision, Reason, decide
from .parser import TrustReport, parse_report, parse_text
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
        report, digest = self._snapshot(report_path)
        return self._check_report(report, digest)

    @staticmethod
    def _snapshot(report_path):
        data = Path(report_path).read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if str(report_path).lower().endswith(".pdf"):
            from pypdf import PdfReader
            reader = PdfReader(BytesIO(data))
            try:
                raw = "\n".join(p.extract_text(extraction_mode="layout") or "" for p in reader.pages)
            except TypeError:
                raw = "\n".join(p.extract_text() or "" for p in reader.pages)
        else:
            raw = data.decode("utf-8")
        return parse_text(raw, str(report_path)), digest

    def _check_report(self, report, digest):
        ta = assess(report)
        d = decide(report, self.context, ta)
        d.report_sha256 = digest
        if self.log:
            audit.record(
                "decision",
                {
                    "report": report.path,
                    "report_sha256": digest,
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

    def guard(self, report: str, payload_arg: str = "payload"):
        """Guard one text-publishing operation whose only argument is the payload.

        Context/destination must be fixed by the guarded callable. Arbitrary tool
        parameters require a richer action schema; they are deliberately refused.
        """
        def deco(fn):
            params = list(inspect.signature(fn).parameters.values())
            if (len(params) != 1 or params[0].name != payload_arg
                    or params[0].kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)):
                raise ValueError("Guarded action must accept exactly one named text payload argument")
            if inspect.iscoroutinefunction(fn):
                raise ValueError("Async actions are not supported by this synchronous guard")
            @functools.wraps(fn)
            def wrapper(*a, **kw):
                bound = inspect.signature(fn).bind(*a, **kw)
                bound.apply_defaults()
                payload = bound.arguments[payload_arg]
                parsed, digest = self._snapshot(report)
                d = self._check_report(parsed, digest)
                if not isinstance(payload, str) or " ".join(payload.split()) != " ".join(parsed.analyzed_text.split()):
                    d = replace(d, action=BLOCK, tripped=True,
                                reasons=d.reasons + [Reason("Payload binding", "Execution text does not match the report's analyzed text.", BLOCK)])
                event = {"action_name": fn.__module__ + "." + fn.__qualname__,
                         "context": self.context, "report_sha256": digest,
                         "payload_sha256": hashlib.sha256(payload.encode()).hexdigest() if isinstance(payload, str) else None,
                         "decision": d.action}
                if d.tripped:
                    if self.log:
                        audit.record("execution_blocked", event)
                    raise FuseTripped(d)
                if self.log:
                    audit.record("execution_started", event)
                try:
                    result = fn(*a, **kw)
                except Exception as exc:
                    if self.log:
                        audit.record("execution_failed", {**event, "error_type": type(exc).__name__})
                    raise
                if self.log:
                    audit.record("execution_completed", event)
                return result

            return wrapper

        return deco
