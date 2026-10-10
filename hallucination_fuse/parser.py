"""Parse an exported Ezio trust report (PDF) into structured evidence.

Built against real Ezio v1 exports. Layout:
  Analyzed text · Verdict · Issues found: N · Module Scores (█░ bars, or "Not scored")
  Claim analysis → Claim N / text / Verdict: X[: detail] / What|Amount checks → (Source k)
                   / Sources: ● k Type · Title — Published: date
  Findings → "<Module> (NOTE|WARNING|CRITICAL|N/A)" + body / Reasoning / Evidence
  Methodology (ignored)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import List, Optional, Dict

MODULES = ["Accuracy", "Bias", "Tone", "Agreement"]
SOURCE_TYPES = ["Official", "Academic", "Reference", "News", "Other"]


@dataclass
class Source:
    type: str
    title: str
    date: Optional[str] = None
    idx: Optional[int] = None


@dataclass
class Check:
    """One sub-check Ezio ran on a claim, e.g. What / Amount."""

    aspect: str  # What | Amount | Date | Who ...
    status: str  # match | not_found | different | contradicts
    detail: str
    source_idx: Optional[int] = None


@dataclass
class Claim:
    id: int
    text: str
    verdict: str  # Supported | Not enough evidence | Contradicted ...
    verdict_detail: Optional[str] = None  # e.g. "detail" in "Contradicted: detail"
    sources: List[Source] = field(default_factory=list)
    checks: List[Check] = field(default_factory=list)

    def source(self, idx: Optional[int]) -> Optional[Source]:
        return next((s for s in self.sources if s.idx == idx), None) if idx else None


@dataclass
class Finding:
    kind: str  # Sentiment | Factual | Framing | Sycophancy
    title: str
    severity: str  # NOTE | WARNING | CRITICAL | N/A
    text: str
    evidence: List[str] = field(default_factory=list)


@dataclass
class TrustReport:
    path: str
    topic: str
    analyzed_text: str
    verdict: str
    issue_count: Optional[int]
    scores: Dict[str, Optional[int]]  # module -> filled blocks (0-5), None = not scored
    score_notes: Dict[str, str]
    claims: List[Claim]
    findings: List[Finding]
    raw_text: str

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("raw_text")
        return d


# ---------------------------------------------------------------- extraction

def extract_text(pdf_path: str) -> str:
    from pypdf import PdfReader

    reader = PdfReader(pdf_path)
    try:
        return "\n".join(p.extract_text(extraction_mode="layout") or "" for p in reader.pages)
    except TypeError:  # older pypdf
        return "\n".join(p.extract_text() or "" for p in reader.pages)


def _clean(s: str) -> str:
    return " ".join(s.split())


def _between(text: str, start: str, ends: List[str]) -> str:
    m = re.search(start, text, re.M)
    if not m:
        return ""
    rest = text[m.end():]
    cut = len(rest)
    for e in ends:
        x = re.search(e, rest, re.M)
        if x:
            cut = min(cut, x.start())
    return rest[:cut]


def _bullets(block: str) -> List[str]:
    """Split a block into ●-bullets, joining wrapped continuation lines."""
    items, cur = [], None
    for ln in block.split("\n"):
        s = ln.strip()
        if not s:
            continue
        if s.startswith("●"):
            if cur is not None:
                items.append(_clean(cur))
            cur = s.lstrip("● ").strip()
        elif cur is not None:
            cur += " " + s
    if cur is not None:
        items.append(_clean(cur))
    return items


def _guess_topic(text: str) -> str:
    t = text.lower()
    for key, label in [
        ("nuclear", "Nuclear energy"),
        ("remote work", "Work from home"),
        ("work from home", "Work from home"),
        ("electric vehicle", "EV market"),
        (" ev ", "EV market"),
        (" evs", "EV market"),
        ("agi", "AGI timelines"),
        ("artificial general intelligence", "AGI timelines"),
    ]:
        if key in t:
            return label
    return "Unknown"


# ---------------------------------------------------------------- sections

def _parse_sources(block: str) -> List[Source]:
    out = []
    type_alt = "|".join(SOURCE_TYPES)
    for b in _bullets(block):
        m = re.match(rf"(\d+)\s+({type_alt})\s*·\s*(.+)$", b)
        if not m:
            continue
        rest = m.group(3)
        date = None
        dm = re.search(r"—\s*Published:\s*(\d{4}-\d{2}-\d{2})", rest)
        if dm:
            date = dm.group(1)
            rest = rest[: dm.start()]
        out.append(Source(type=m.group(2), title=rest.strip(" —"), date=date, idx=int(m.group(1))))
    return out


def _parse_checks(block: str) -> List[Check]:
    out = []
    for b in _bullets(block):
        m = re.match(r"(What|Amount|Date|Who|Where|When|Number|Quote)\s*:\s*(.+)$", b)
        if not m:
            continue
        body = m.group(2)
        src = re.search(r"\(Source (\d+)\)", body)
        low = body.lower()
        if "not found" in low:
            status = "not_found"
        elif "different detail" in low:
            status = "different"
        elif "sources say" in low or "contradict" in low:
            status = "contradicts"
        elif "matches" in low:
            status = "match"
        else:
            status = "unknown"
        out.append(Check(aspect=m.group(1), status=status, detail=body, source_idx=int(src.group(1)) if src else None))
    return out


def _parse_claims(section: str) -> List[Claim]:
    claims = []
    chunks = re.split(r"^\s*Claim (\d+)\s*$", section, flags=re.M)
    # chunks: [preamble, id1, body1, id2, body2, ...]
    for i in range(1, len(chunks), 2):
        cid, body = int(chunks[i]), chunks[i + 1]
        vm = re.search(r"^\s*Verdict:\s*(.+)$", body, re.M)
        text = _clean(body[: vm.start()]) if vm else ""
        verdict, vdetail = "Unknown", None
        if vm:
            v = vm.group(1).strip()
            if ":" in v:
                verdict, vdetail = [x.strip() for x in v.split(":", 1)]
            else:
                verdict = v
        src_block = _between(body, r"^\s*Sources:\s*$", [r"\Z"])
        check_block = body[vm.end():] if vm else body
        sm = re.search(r"^\s*Sources:\s*$", check_block, re.M)
        if sm:
            check_block = check_block[: sm.start()]
        claims.append(
            Claim(
                id=cid,
                text=text,
                verdict=verdict,
                verdict_detail=vdetail,
                sources=_parse_sources(src_block),
                checks=_parse_checks(check_block),
            )
        )
    return claims


_KIND = [("sentiment", "Sentiment"), ("factual", "Factual"), ("framing", "Framing"), ("sycophan", "Sycophancy"), ("tone", "Tone"), ("bias", "Framing")]


def _parse_findings(section: str) -> List[Finding]:
    out = []
    heads = list(re.finditer(r"^\s*([A-Z][A-Za-z &/]+?)\s*\((NOTE|WARNING|CRITICAL|N/A)\)\s*$", section, re.M))
    for j, h in enumerate(heads):
        body = section[h.end(): heads[j + 1].start() if j + 1 < len(heads) else len(section)]
        ev_m = re.search(r"^\s*Evidence:\s*$", body, re.M)
        evidence = _bullets(body[ev_m.end():]) if ev_m else []
        main = body[: ev_m.start()] if ev_m else body
        title = h.group(1).strip()
        kind = next((k for key, k in _KIND if key in title.lower()), title)
        out.append(Finding(kind=kind, title=title, severity=h.group(2), text=_clean(main), evidence=[e.strip('"') for e in evidence]))
    return out


def parse_report(pdf_path: str) -> TrustReport:
    path = str(Path(pdf_path))
    raw = extract_text(path) if path.lower().endswith(".pdf") else Path(path).read_text()
    return parse_text(raw, path)


def parse_text(raw: str, path: str = "<text>") -> TrustReport:
    analyzed = _clean(_between(raw, r"^\s*Analyzed text\s*$", [r"^\s*Verdict\s*$"]))
    verdict = _clean(_between(raw, r"^\s*Verdict\s*$", [r"^\s*Issues found", r"^\s*Module Scores"]))
    im = re.search(r"Issues found:\s*(\d+)", raw)

    scores: Dict[str, Optional[int]] = {}
    notes: Dict[str, str] = {}
    for mod in MODULES:
        m = re.search(rf"^\s*{mod}:\s*(.+)$", raw, re.M)
        if not m:
            scores[mod] = None
            notes[mod] = "missing"
            continue
        line = m.group(1)
        bar = re.search(r"[█░]{5}", line)
        if bar:
            scores[mod] = bar.group(0).count("█")
            fm = re.search(r"full\s*=\s*(.+)$", line)
            notes[mod] = fm.group(1).strip() if fm else ""
        else:
            scores[mod] = None
            notes[mod] = line.strip()  # e.g. "Not scored"

    claims_sec = _between(raw, r"^\s*Claim analysis\s*$", [r"^\s*Findings\s*$"])
    findings_sec = _between(raw, r"^\s*Findings\s*$", [r"^\s*Methodology\s*$", r"Generated by ezio"])

    return TrustReport(
        path=path,
        topic=_guess_topic(analyzed),
        analyzed_text=analyzed,
        verdict=verdict,
        issue_count=int(im.group(1)) if im else None,
        scores=scores,
        score_notes=notes,
        claims=_parse_claims(claims_sec),
        findings=_parse_findings(findings_sec),
        raw_text=raw,
    )
