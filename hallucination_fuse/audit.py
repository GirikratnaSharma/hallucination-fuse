"""Hash-linked audit records; not immutable storage.

An independently retained expected_head detects tail deletion or full replacement.
Without it, verification checks only the internal consistency of remaining data.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
from pathlib import Path
from datetime import datetime, timezone
from contextlib import contextmanager

DEFAULT_LOG = Path(__file__).resolve().parent.parent / 'audit' / 'fuse_audit.jsonl'


@contextmanager
def _lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(path.suffix + '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _scan(path):
    prev, n = 'GENESIS', 0
    if not path.exists():
        return True, n, None, prev
    with path.open() as stream:
        for i, line in enumerate(stream, 1):
            try:
                e = json.loads(line)
                h = e.pop('hash')
                if e['prev'] != prev or hashlib.sha256(json.dumps(e, sort_keys=True, default=str).encode()).hexdigest() != h:
                    return False, n, i, prev
            except (ValueError, KeyError, TypeError, AttributeError):
                return False, n, i, prev
            prev, n = h, n + 1
    return True, n, None, prev


def record(event: str, payload: dict, path: Path = DEFAULT_LOG) -> str:
    path = Path(path)
    with _lock(path):
        ok, _, bad, prev = _scan(path)
        if not ok:
            raise ValueError('Audit log is corrupt at line %s; refusing append' % bad)
        entry = {'ts': datetime.now(timezone.utc).isoformat(), 'event': event, 'payload': payload, 'prev': prev}
        entry['hash'] = hashlib.sha256(json.dumps(entry, sort_keys=True, default=str).encode()).hexdigest()
        with path.open('a') as stream:
            stream.write(json.dumps(entry, sort_keys=True, default=str) + '\n')
        return entry['hash']


def verify(path: Path = DEFAULT_LOG, expected_head: str | None = None) -> tuple:
    """Return (ok, count, bad_line). A checkpoint mismatch uses count + 1."""
    path = Path(path)
    with _lock(path):
        ok, n, bad, head = _scan(path)
        if ok and expected_head is not None and head != expected_head:
            return False, n, n + 1
        return ok, n, bad


def file_sha256(p: str) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()
