"""Claude Code transcripts and Codex rollouts -> one event per (session, day, project).

Attribution (see BUILD_PROMPT.md §4):
- Claude: every transcript runs from cwd Downloads, so only the paths in tool calls count.
- Codex: the session cwd gives a default project; paths in tool-call arguments override it.
- Codex rollouts imported from Claude are skipped when the Claude original still exists,
  and counted as Claude sessions when it does not (the import is then the only copy).
- guardian_review / subagent Codex sessions are not separate sessions.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from ..classify import classify, to_local
from ..config import Config
from ..db import cache_get, cache_put, file_stamp

PATH_KEYS = ("file_path", "path", "notebook_path", "command", "pattern", "glob", "cwd", "workdir")
SKIP_PROMPT = ("<", "# AGENTS.md", "Caveat:", "[Request interrupted", "This session is being continued")


def _first_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for b in content:
            if isinstance(b, dict) and b.get("type") in ("text", "input_text") and b.get("text"):
                return b["text"]
    return ""


def _clean_prompt(t: str) -> str:
    t = " ".join(t.split())
    return "" if not t or t.startswith(SKIP_PROMPT) else t[:160]


class Agg:
    def __init__(self):
        self.d = defaultdict(lambda: {"calls": 0, "first": None, "last": None, "prompt": ""})

    def add(self, sid, day, ts, project, prompt=""):
        a = self.d[(sid, day, project)]
        a["calls"] += 1
        a["first"] = min(a["first"] or ts, ts)
        a["last"] = max(a["last"] or ts, ts)
        if prompt and not a["prompt"]:
            a["prompt"] = prompt


def _claude_file(f: Path, cfg: Config) -> dict:
    agg, titles, prompts = Agg(), {}, {}
    pending_prompt: dict[str, str] = {}
    with f.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            t, sid = r.get("type"), r.get("sessionId") or f.stem
            if t == "ai-title" and r.get("aiTitle"):
                titles[sid] = r["aiTitle"]
                continue
            msg = r.get("message") or {}
            if t == "user" and not r.get("isSidechain"):
                p = _clean_prompt(_first_text(msg.get("content")))
                if p:
                    pending_prompt[sid] = p
                    prompts.setdefault(sid, p)
                continue
            if t != "assistant" or not isinstance(msg.get("content"), list):
                continue
            loc = to_local(r.get("timestamp", ""), cfg)
            if not loc:
                continue
            for b in msg["content"]:
                if not (isinstance(b, dict) and b.get("type") == "tool_use"):
                    continue
                inp = b.get("input") or {}
                text = " ".join(str(inp[k]) for k in PATH_KEYS if isinstance(inp.get(k), (str, list)))
                proj = classify(text, cfg)
                if proj:
                    agg.add(sid, loc[1], loc[0], proj, pending_prompt.get(sid, ""))
    return {"rows": [[k[0], k[1], k[2], v] for k, v in agg.d.items()], "titles": titles, "prompts": prompts}


def _cwd_project(cwd: str, cfg: Config) -> str | None:
    """Project implied by a session cwd, or None for general-purpose folders."""
    tail = cwd.replace("\\", "/").rstrip("/").split("/")[-1].lower()
    return None if tail in cfg.generic_cwds else classify(cwd, cfg)


def _codex_file(f: Path, cfg: Config) -> dict:
    agg = Agg()
    meta, base, prompt, first_prompt = {}, None, "", ""
    with f.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            t, p = r.get("type"), r.get("payload") or {}
            if t == "session_meta":
                meta = {k: p.get(k) for k in ("id", "cwd", "thread_source", "source", "originator")}
                base = _cwd_project(p.get("cwd") or "", cfg)
                continue
            if t == "turn_context" and p.get("cwd"):
                base = _cwd_project(p["cwd"], cfg) or base
                continue
            if t != "response_item":
                continue
            loc = to_local(r.get("timestamp", ""), cfg)
            if not loc:
                continue
            pt = p.get("type")
            if pt == "message" and p.get("role") == "user":
                c = _clean_prompt(_first_text(p.get("content")))
                if c:
                    prompt = c
                    first_prompt = first_prompt or c
                continue
            if pt in ("function_call", "custom_tool_call", "local_shell_call"):
                text = str(p.get("arguments") or p.get("input") or (p.get("action") or {}).get("command") or "")
                proj = classify(text, cfg) or base
                if proj:
                    agg.add(meta.get("id") or f.stem, loc[1], loc[0], proj, prompt)
    return {"rows": [[k[0], k[1], k[2], v] for k, v in agg.d.items()], "meta": meta, "prompt": first_prompt}


def _cached(con, f: Path, fn, cfg):
    key, stamp = f"ai:{f}", file_stamp(f)
    hit = cache_get(con, key, stamp)
    if hit is None:
        hit = fn(f, cfg)
        cache_put(con, key, stamp, hit)
    return hit


def _event(sid, day, project, a, tool, title):
    return dict(ts=a["first"], day=day, project=project, source=tool, kind="session", tool=tool,
                title=(title or a["prompt"] or "session")[:160],
                detail=f"{a['calls']} tool calls, {a['first'][11:16]}–{a['last'][11:16]} UTC"
                       + (f"\nprompt: {a['prompt']}" if a["prompt"] and a["prompt"] != title else ""),
                ref=sid, weight=a["calls"])


def collect(cfg: Config, con) -> tuple[list[dict], list[dict], list[str]]:
    events, notes = [], []
    # Claude Code
    n_files = 0
    for base in cfg.claude_logs:
        if not base.exists():
            notes.append(f"claude: {base} not found")
            continue
        for f in sorted(base.glob("*/*.jsonl")) + sorted(base.glob("*/*/subagents/*.jsonl")):
            res = _cached(con, f, _claude_file, cfg)
            n_files += 1
            for sid, day, proj, a in res["rows"]:
                events.append(_event(sid, day, proj, a, "claude", res["titles"].get(sid)))
    notes.append(f"claude: {n_files} transcripts")

    # Codex
    imports = {}
    if cfg.codex_imports and cfg.codex_imports.exists():
        for rec in json.loads(cfg.codex_imports.read_text(encoding="utf-8")).get("records", []):
            imports[rec.get("imported_thread_id")] = rec
    names = {}
    for idx in cfg.codex_index:
        if idx.exists():
            for line in idx.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    r = json.loads(line)
                    names[r["id"]] = r.get("thread_name") or ""
                except (ValueError, KeyError):
                    pass
    stats = defaultdict(int)
    for base in cfg.codex_sessions:
        if not base.exists():
            notes.append(f"codex: {base} not found")
            continue
        for f in sorted(base.rglob("*.jsonl")):
            res = _cached(con, f, _codex_file, cfg)
            meta = res["meta"]
            sid = meta.get("id") or f.stem
            src = str(meta.get("source") or "")
            if meta.get("thread_source") in ("guardian_review", "subagent") or "subagent" in src:
                stats["subagent skipped"] += 1
                continue
            tool, title = "codex", names.get(sid) or res["prompt"]
            if sid in imports:
                rec = imports[sid]
                orig = Path(str(rec.get("source_path", "")).replace("\\\\?\\", ""))
                if orig.exists():
                    stats["import skipped (Claude original exists)"] += 1
                    continue
                tool, title = "claude", rec.get("title") or title
                stats["import kept (Claude original gone)"] += 1
            else:
                stats["native"] += 1
            for sid2, day, proj, a in res["rows"]:
                events.append(_event(sid2, day, proj, a, tool, title))
    notes.append("codex: " + ", ".join(f"{v} {k}" for k, v in sorted(stats.items())))
    return events, [], notes
