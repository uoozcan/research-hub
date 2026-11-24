"""Decision records, dated reports, manual workstreams and vault session logs."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import yaml

from ..config import Config, is_guarded

DATE_RE = re.compile(r"(20\d{2})[-_]?(\d{2})[-_]?(\d{2})")
TEXT_KEYS = ("decision", "purpose", "title", "summary", "change", "rule", "problem", "finding")
LOG_HEAD = re.compile(r"^##\s+(\d{1,2}:\d{2})\s+(claude|codex|setup)\b\s*[—-]*\s*(.*)$", re.I)


def _date_from_name(name: str) -> str | None:
    m = DATE_RE.search(name)
    if not m:
        return None
    y, mo, d = m.groups()
    try:
        return datetime(int(y), int(mo), int(d)).strftime("%Y-%m-%d")
    except ValueError:
        return None


def _mtime_day(p: Path, cfg: Config) -> tuple[str, str]:
    dt = datetime.fromtimestamp(p.stat().st_mtime, cfg.tz)
    return dt.isoformat(), dt.strftime("%Y-%m-%d")


def humanize(stem: str) -> str:
    s = DATE_RE.sub("", stem).strip("_- ")
    s = s.replace("_", " ").replace("-", " ").strip()
    return (s[:1].upper() + s[1:]) if s else stem


def _summary(d: dict) -> str:
    for k in TEXT_KEYS:
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, dict):
            for kk in ("choice", "new_primary_method", "primary_singleton_policy", "procedure", "definition"):
                if isinstance(v.get(kk), str):
                    return f"{k}: {v[kk]}"
    return ""


def collect(cfg: Config) -> tuple[list[dict], list[dict], list[str]]:
    events, streams, notes = [], [], []
    for key, proj in cfg.projects.items():
        n_dec = 0
        for ddir in proj.decisions:
            for f in sorted(ddir.glob("*.json")) if ddir.exists() else []:
                try:
                    d = json.loads(f.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                day = _date_from_name(f.name) or (d.get("date") if isinstance(d.get("date"), str) else None)
                ts = f"{day}T12:00:00" if day else _mtime_day(f, cfg)[0]
                day = day or _mtime_day(f, cfg)[1]
                events.append(dict(ts=ts, day=day[:10], project=key, source="decision", kind="decision",
                                   title=humanize(f.stem), detail=_summary(d)[:600], ref=str(f)))
                n_dec += 1
        n_rep = 0
        for rdir in proj.reports:
            if not rdir.exists():
                continue
            for f in sorted(rdir.glob("*.md")) + sorted(rdir.glob("*/*.md")):
                if is_guarded(f):
                    continue
                day = _date_from_name(f.name)
                named = f.stem.upper()
                if not day and not any(k in named for k in ("FINDINGS", "STATUS", "RESULT", "SUMMARY", "TODO")):
                    continue
                ts, mday = _mtime_day(f, cfg)
                events.append(dict(ts=f"{day}T12:00:00" if day else ts, day=day or mday, project=key,
                                   source="report", kind="report", title=humanize(f.stem),
                                   detail=str(f.relative_to(rdir.parent)), ref=str(f)))
                n_rep += 1
        if proj.decisions or proj.reports:
            notes.append(f"records: {key}: {n_dec} decisions, {n_rep} reports")
        if proj.workstreams and proj.workstreams.exists():
            for w in yaml.safe_load(proj.workstreams.read_text(encoding="utf-8")) or []:
                streams.append(dict(project=key, title=w["title"], start=str(w["start"]),
                                    end=str(w.get("end") or w["start"]), status=w.get("status", "done"),
                                    source="manual", ref=w.get("evidence", ""), detail=w.get("note", "")))
        for vault in proj.vaults:
            for f in sorted((vault / "log").glob("*.md")) if (vault / "log").exists() else []:
                day = _date_from_name(f.name)
                if not day:
                    continue
                cur = None
                for line in f.read_text(encoding="utf-8").splitlines():
                    m = LOG_HEAD.match(line)
                    if m:
                        cur = dict(ts=f"{day}T{m.group(1).zfill(5)}:00", day=day, project=key, source="vault",
                                   kind="handoff", title=m.group(3).strip() or "session", detail="",
                                   ref=str(f), tool=m.group(2).lower())
                        events.append(cur)
                    elif cur is not None and line.strip():
                        cur["detail"] = (cur["detail"] + "\n" + line).strip()[:1200]
    return events, streams, notes
