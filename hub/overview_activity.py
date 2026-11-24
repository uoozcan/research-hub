"""Project activity rollups for Overview, using the existing local index."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from . import config
from .db import connect

CATEGORIES = {
    "commit": "Commit",
    "release": "Release",
    "decision": "Decision",
    "report": "Report",
    "handoff": "Handoff",
    "files": "File changes",
    "ai": "AI session",
    "focus": "Focus session",
    "other": "Other activity",
}


def _rows(keys, start, end):
    projects = ",".join("?" for _ in keys)
    guard = "".join(" AND instr(lower(coalesce(ref,'')), ?) = 0" for _ in config.GUARDED)
    sql = f"""
        SELECT day, project,
               CASE
                 WHEN source = 'git' AND kind = 'release' THEN 'release'
                 WHEN source = 'git' THEN 'commit'
                 WHEN source = 'decision' THEN 'decision'
                 WHEN source = 'report' THEN 'report'
                 WHEN source = 'vault' THEN 'handoff'
                 WHEN source = 'file' THEN 'files'
                 WHEN source IN ('claude', 'codex') THEN 'ai'
                 ELSE 'other'
               END AS category,
               ts, source, title, ref, weight, NULL AS minutes
          FROM events
         WHERE project IN ({projects}) AND day BETWEEN ? AND ? {guard}
        UNION ALL
        SELECT day, project, 'focus', started, 'pomodoro', NULL, NULL, 1, minutes
          FROM pomodoros
         WHERE project IN ({projects}) AND day BETWEEN ? AND ? AND kind = 'focus'
    """
    bounds = (start.isoformat(), end.isoformat())
    return sql, (*keys, *bounds, *config.GUARDED, *keys, *bounds)


def _daily_rows(rows):
    counts = defaultdict(dict)
    for r in rows:
        counts[r["day"]][r["category"]] = r["entries"]
    return [{"day": day, "total": sum(by_category.values()), "counts": by_category}
            for day, by_category in sorted(counts.items())]


def window(keys, days, today: date, timezone: str):
    start = today - timedelta(days=days - 1)
    sql, params = _rows(keys, start, today)
    with connect() as db:
        refreshed = db.execute("SELECT value FROM meta WHERE key='refreshed'").fetchone()
        rows = db.execute(
            f"SELECT day, category, COUNT(*) AS entries FROM ({sql}) GROUP BY day, category", params
        ).fetchall()
    return {
        "start": start.isoformat(), "end": today.isoformat(), "timezone": timezone,
        "refreshed": refreshed["value"] if refreshed else None, "days": _daily_rows(rows),
    }


def _record_path(ref, cfg):
    path = Path(ref)
    try:
        if path.is_absolute() and config.is_allowed(path, cfg) and not config.is_guarded(path.resolve()) and path.is_file():
            return ref
    except (OSError, ValueError):
        pass
    return None


def _entry(row, cfg):
    category = row["category"]
    entry = {
        "project": row["project"], "category": category, "timestamp": row["ts"],
        "title": row["title"] or CATEGORIES[category],
    }
    if category == "ai":
        entry["title"] = "Codex session" if row["source"] == "codex" else "Claude Code session"
    elif category == "focus":
        entry["title"] = f"{row['minutes']:g} min focus session" if row["minutes"] else "Focus session"
    elif category == "files":
        entry["files"] = row["weight"] or 1
    elif category in ("commit", "release") and row["ref"]:
        entry["reference"] = row["ref"]
    elif category in ("decision", "report", "handoff") and row["ref"]:
        path = _record_path(row["ref"], cfg)
        if path:
            entry["path"] = path
    return entry


def day_detail(keys, day: date, cfg):
    sql, params = _rows(keys, day, day)
    with connect() as db:
        rows = db.execute(
            f"SELECT * FROM ({sql}) ORDER BY julianday(ts) DESC, project, category, title, ref", params
        ).fetchall()
    entries = [_entry(row, cfg) for row in rows]
    counts = defaultdict(int)
    for entry in entries:
        counts[entry["category"]] += 1
    return {"day": day.isoformat(), "total": len(entries), "counts": dict(counts), "entries": entries}
