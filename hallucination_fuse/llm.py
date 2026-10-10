"""Optional Claude layer. Everything works without it (deterministic fallback).

Enabled when ANTHROPIC_API_KEY (or another Anthropic credential) is available.
"""
from __future__ import annotations

import os
from typing import Optional

MODEL = os.environ.get("FUSE_MODEL", "claude-opus-5-5")


def available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def ask(system: str, prompt: str, max_tokens: int = 4000) -> Optional[str]:
    if not available():
        return None
    try:
        import anthropic

        client = anthropic.Anthropic()
        resp = client.beta.messages.create(
            model=MODEL,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            output_config={"effort": "medium"},
            betas=["server-side-fallback-2026-07-01"],
            extra_body={"fallbacks": "default"},
        )
        if resp.stop_reason == "refusal":
            return None
        return "".join(b.text for b in resp.content if b.type == "text").strip() or None
    except Exception as e:  # never let the LLM layer break the fuse
        print("[llm] unavailable: %s" % e)
        return None
