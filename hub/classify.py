"""Attribute a path, command or cwd string to a project."""

from __future__ import annotations

import re
from datetime import datetime

from .config import Config

# Mentions that are about the tools themselves, not project work.
IGNORE = ("/.claude/", "/.codex/", "/memory/", "/.ai-vault/handoff")


def norm(text: str) -> str:
    return text.lower().replace("\\", "/")


def classify(text: str, cfg: Config) -> str | None:
    """Return the project whose match string occurs deepest (rightmost) in `text`.

    Each whitespace-separated token is classified on its own, and the token-level winners are
    tallied, so a command touching two projects goes to the one it touches most.
    """
    if not text:
        return None
    tally: dict[str, int] = {}
    for token in re.split(r"[\s'\"`;|&()<>=,]+", norm(text)):
        if len(token) < 6 or any(i in token for i in IGNORE):
            continue
        best, best_pos, best_len = None, -1, 0
        for key, proj in cfg.projects.items():
            for m in proj.match:
                pos = token.rfind(m)
                if pos > best_pos or (pos == best_pos and pos >= 0 and len(m) > best_len):
                    if pos >= 0:
                        best, best_pos, best_len = key, pos, len(m)
        if best:
            tally[best] = tally.get(best, 0) + 1
    if not tally:
        return None
    return max(tally.items(), key=lambda kv: kv[1])[0]


def to_local(ts: str, cfg: Config) -> tuple[str, str] | None:
    """ISO-8601 (UTC or offset) -> (iso_utc, local day)."""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=cfg.tz)
    return dt.isoformat(), dt.astimezone(cfg.tz).strftime("%Y-%m-%d")
