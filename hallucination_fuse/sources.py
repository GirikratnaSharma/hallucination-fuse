"""PULL_SOURCE: fetch an independent second source for claims the report couldn't verify.

Uses the public Wikipedia search API (no key). A hit is treated as a *lead*, rated
'Other' quality — it can corroborate, but it never silently upgrades a claim to
Supported. The human still decides.
"""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from typing import List, Optional

STOP = set("the a an of and or to in on for with by is are was were be that this it as at from will has have more than".split())


def _query(claim: str) -> str:
    words = [w for w in re.findall(r"[A-Za-z0-9%$.]+", claim) if w.lower() not in STOP]
    return " ".join(words[:10])


def lookup(claim: str, timeout: float = 6.0) -> List[dict]:
    q = _query(claim)
    url = "https://en.wikipedia.org/w/api.php?" + urllib.parse.urlencode(
        {"action": "query", "list": "search", "srsearch": q, "format": "json", "srlimit": 3}
    )
    req = urllib.request.Request(url, headers={"User-Agent": "hallucination-fuse/0.1 (hackathon demo)"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.load(r)
    except Exception as e:  # network optional
        return [{"error": str(e)}]
    out = []
    for hit in data.get("query", {}).get("search", []):
        snippet = re.sub(r"<[^>]+>", "", hit.get("snippet", ""))
        out.append(
            {
                "type": "Other",
                "title": "Wikipedia: " + hit["title"],
                "url": "https://en.wikipedia.org/wiki/" + urllib.parse.quote(hit["title"].replace(" ", "_")),
                "snippet": snippet,
                "date": (hit.get("timestamp") or "")[:10],
            }
        )
    return out
