"""Append-only, hash-chained audit log. Any edit to a past entry breaks the chain."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

DEFAULT_LOG = Path(__file__).resolve().parent.parent / "audit" / "fuse_audit.jsonl"


def _last_hash(path: Path) -> str:
    if not path.exists():
        return "GENESIS"
    last = None
    with path.open() as f:
        for line in f:
            if line.strip():
                last = line
    return json.loads(last)["hash"] if last else "GENESIS"


def record(event: str, payload: dict, path: Path = DEFAULT_LOG) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "event": event, "payload": payload, "prev": _last_hash(path)}
    body = json.dumps(entry, sort_keys=True, default=str)
    entry["hash"] = hashlib.sha256(body.encode()).hexdigest()
    with path.open("a") as f:
        f.write(json.dumps(entry, sort_keys=True, default=str) + "\n")
    return entry["hash"]


def verify(path: Path = DEFAULT_LOG) -> tuple:
    """Return (ok, n_entries, first_bad_line)."""
    prev = "GENESIS"
    n = 0
    if not path.exists():
        return True, 0, None
    with path.open() as f:
        for i, line in enumerate(f, 1):
            if not line.strip():
                continue
            e = json.loads(line)
            h = e.pop("hash")
            if e["prev"] != prev or hashlib.sha256(json.dumps(e, sort_keys=True, default=str).encode()).hexdigest() != h:
                return False, n, i
            prev = h
            n += 1
    return True, n, None


def file_sha256(p: str) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()
