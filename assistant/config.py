from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


def context_mode() -> str:
    mode = (os.getenv("CONTEXT_MODE") or "hybrid").strip().lower()
    return mode if mode in ("full", "hybrid") else "hybrid"


def max_context_tokens() -> int:
    raw = os.getenv("MAX_CONTEXT_TOKENS") or "6500"
    try:
        return max(500, int(raw))
    except ValueError:
        return 6500
