"""Parse an exported Ezio trust report (PDF) into structured evidence.

The Ezio PDF is text-based. We extract text with pypdf and then pull out:
analyzed text, verdict, module score bars, claims (+ verdicts + sources), findings.
The parser is deliberately tolerant: Ezio's layout may shift, so every section
is optional and the raw text is always kept for the LLM layer.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import List, Optional, Dict

FILLED = "█"
EMPTY = "░"

MODULES = ["Accuracy", "Bias", "Tone", "Agreement"]

CLAIM_VERDICTS = [
    "Supported",
    "Partially supported",
    "Mostly supported",
    "Not enough evidence",
    "Insufficient evidence",
    "Unverified",
    "Disputed",
    "Contradicted",
    "Refuted",
    "Misleading",
    "False",
]

SOURCE_TYPES = ["Official", "News", "Academic", "Other"]
SEVERITIES = ["CRITICAL", "WARNING", "NOTE"]
FINDING_KINDS = ["Sentiment", "Factual", "Framing", "Sycophancy", "Bias", "Tone"]


@dataclass
class Source:
    type: str
    title: str
    date: Optional[str] = None


@dataclass
class Claim:
    id: int
    text: str
    verdict: str
    sources: List[Source] = field(default_factory=list)


@dataclass
class Finding:
    kind: str
    severity: str
    text: str


@dataclass
class TrustReport:
    path: str
    topic: str
    analyzed_text: str
    verdict: str
    issue_count: Optional[int]
    scores: Dict[str, Optional[int]]  # module -> filled blocks (0-5)
    claims: List[Claim]
    findings: List[Finding]
    raw_text: str

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("raw_text")
        return d


def extract_text(pdf_path: str) -> str:
    from pypdf import PdfReader

    reader = PdfReader(pdf_path)
    return "\n".join((p.extract_text() or "") for p in reader.pages)


def _bar_value(segment: str) -> Optional[int]:
    """Count filled blocks in a █░ bar (Tips: scores are bars, not numbers)."""
    m = re.search(r"[█░▓▒■□]{3,}", segment)
    if not m:
        n = re.search(r"\b([0-5])\s*/\s*5\b", segment)
        return int(n.group(1)) if n else None
    bar = m.group(0)
    return sum(1 for c in bar if c in "█▓■")


def _section(text: str, start_pat: str, end_pats: List[str]) -> str:
    m = re.search(start_pat, text, re.I)
    if not m:
        return ""
    rest = text[m.end():]
    end = len(rest)
    for p in end_pats:
        e = re.search(p, rest, re.I)
        if e and e.start() < end:
            end = e.start()
    return rest[:end].strip()


def _guess_topic(text: str) -> str:
    t = text.lower()
    for key, label in [
        ("nuclear", "Nuclear energy"),
        ("work from home", "Work from home"),
        ("remote work", "Work from home"),
        ("electric vehicle", "EV market"),
        (" ev ", "EV market"),
        ("agi", "AGI timelines"),
        ("artificial general intelligence", "AGI timelines"),
    ]:
        if key in t:
            return label
    return "Unknown"


def _parse_sources(block: str) -> List[Source]:
    sources: List[Source] = []
    type_alt = "|".join(SOURCE_TYPES)
    for m in re.finditer(
        rf"\b({type_alt})\b[\s:·\-–|]*(.+?)(?=(?:\b(?:{type_alt})\b[\s:·\-–|])|$)",
        block,
        re.S,
    ):
        body = " ".join(m.group(2).split())
        date_m = re.search(
            r"(\d{4}-\d{2}-\d{2}|\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.? \d{1,2},? \d{4}|\b\d{1,2} (?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* \d{4}|\b(?:19|20)\d{2}\b)",
            body,
        )
        date = date_m.group(0) if date_m else None
        title = body.replace(date, "").strip(" ·-–|,") if date else body
        if title:
            sources.append(Source(type=m.group(1), title=title[:200], date=date))
    return sources


def _parse_claims(section: str) -> List[Claim]:
    if not section:
        return []
    verdict_alt = "|".join(re.escape(v) for v in sorted(CLAIM_VERDICTS, key=len, reverse=True))
    parts = re.split(rf"({verdict_alt})", section, flags=re.I)
    claims: List[Claim] = []
    # parts: [pre, verdict1, post1, verdict2, post2, ...]
    # Claim text usually precedes its verdict; sources follow it.
    pending_text = parts[0]
    for i in range(1, len(parts), 2):
        verdict = parts[i]
        after = parts[i + 1] if i + 1 < len(parts) else ""
        # Split `after` into this claim's sources and the next claim's text.
        # The next claim text starts after the last source line; heuristically
        # the last sentence-like chunk without a source-type keyword.
        lines = [l for l in after.split("\n") if l.strip()]
        src_lines, next_text = [], []
        seen_src = False
        for ln in lines:
            if re.match(r"\s*(Claim\s*\d+[:.)]?|\d+[.)])\s", ln):
                next_text = [ln]
                seen_src = False
                continue
            if next_text and re.match(r"\s*(Claim\s*\d+[:.)]?|\d+[.)])\s", next_text[0]):
                next_text.append(ln)
                continue
            if re.search(r"\b(" + "|".join(SOURCE_TYPES) + r")\b", ln) or (
                seen_src and re.search(r"\b(19|20)\d{2}\b", ln) and len(ln) < 80
            ):
                if next_text:  # a source after text → text belonged to source titles
                    src_lines.extend(next_text)
                    next_text = []
                src_lines.append(ln)
                seen_src = True
            else:
                next_text.append(ln)
        text = " ".join(pending_text.split())
        text = re.sub(r"^(Claim\s*\d+[:.)]?\s*|\d+[.)]\s*|Sources?:?\s*)", "", text, flags=re.I)
        text = re.sub(r"\bSources?\s*:?\s*$", "", text, flags=re.I).strip(" :-–")
        claims.append(
            Claim(
                id=len(claims) + 1,
                text=text[-400:],
                verdict=_normalize_verdict(verdict),
                sources=_parse_sources("\n".join(src_lines)),
            )
        )
        pending_text = "\n".join(next_text)
    return claims


def _normalize_verdict(v: str) -> str:
    for cv in CLAIM_VERDICTS:
        if cv.lower() == v.lower():
            return cv
    return v


def _parse_findings(section: str) -> List[Finding]:
    if not section:
        return []
    sev_re = re.compile(r"\b(" + "|".join(SEVERITIES) + r")\b")
    findings: List[Finding] = []
    cur = None  # [label, severity, body_lines]
    for ln in [l.strip() for l in section.split("\n") if l.strip()]:
        m = sev_re.search(ln)
        if m:
            label = ln[: m.start()].strip(" :-–·|")
            if not label and cur and cur[2]:
                label = cur[2].pop()  # label sat on its own line above
            if cur:
                findings.append(cur)
            cur = [label, m.group(1), [ln[m.end():].strip(" :-–·|")]]
        elif cur:
            cur[2].append(ln)
    if cur:
        findings.append(cur)
    out = []
    for label, sev, body in findings:
        kind = next((k for k in FINDING_KINDS if k.lower() in label.lower()), None)
        if kind is None:
            kind = next((k for k in FINDING_KINDS if k.lower() in " ".join(body).lower()[:120]), label or "General")
        out.append(Finding(kind=kind, severity=sev, text=" ".join(" ".join(body).split())[:600]))
    return out


def parse_report(pdf_path: str) -> TrustReport:
    path = str(Path(pdf_path))
    raw = extract_text(path) if path.lower().endswith(".pdf") else Path(path).read_text()
    return parse_text(raw, path)


def parse_text(raw: str, path: str = "<text>") -> TrustReport:
    analyzed = _section(
        raw,
        r"Analy[sz]ed\s+text",
        [r"\bVerdict\b", r"Module\s+scores", r"\bAccuracy\b", r"Claim\s+analysis"],
    )
    verdict_sec = _section(raw, r"\bVerdict\b", [r"Module\s+scores", r"\bAccuracy\b", r"Claim\s+analysis"])
    verdict_line = next((l.strip() for l in verdict_sec.split("\n") if l.strip()), "")
    issues_m = re.search(r"(\d+)\s+(?:issues?|flags?|problems?)", verdict_sec or raw, re.I)

    scores: Dict[str, Optional[int]] = {}
    for mod in MODULES:
        m = re.search(rf"\b{mod}\b(.{{0,40}})", raw, re.S)
        scores[mod] = _bar_value(m.group(1)) if m else None

    claims_sec = _section(raw, r"Claim\s+analysis", [r"\bFindings\b"])
    findings_sec = _section(raw, r"\bFindings\b", [r"Generated by", r"ezioai\.io\s*$"])

    return TrustReport(
        path=path,
        topic=_guess_topic(analyzed or raw),
        analyzed_text=analyzed,
        verdict=verdict_line,
        issue_count=int(issues_m.group(1)) if issues_m else None,
        scores=scores,
        claims=_parse_claims(claims_sec),
        findings=_parse_findings(findings_sec),
        raw_text=raw,
    )
