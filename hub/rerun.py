"""Aggregate-only benchmark rerun parsing and snapshot calculations."""

from __future__ import annotations

import csv
import json
import re
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path

from .config import Project

FAILURE_CLASSES = ("storage", "input_transfer", "caller", "parser", "integrity", "other")


def _integer(text: str, names: tuple[str, ...]) -> int | None:
    joined = "|".join(re.escape(name).replace(r"\_", "[-_ ]") for name in names)
    match = re.search(rf"(?i)(?:^|\b)(?:{joined})\s*(?:=|:|\t| )\s*(\d+)", text)
    return int(match.group(1)) if match else None


def _decimal(text: str, names: tuple[str, ...]) -> float | None:
    joined = "|".join(re.escape(name).replace(r"\_", "[-_ ]") for name in names)
    match = re.search(rf"(?i)(?:^|\b)(?:{joined})\s*(?:=|:|\t| )\s*(\d+(?:\.\d+)?)", text)
    return float(match.group(1)) if match else None


def parse_remote_output(text: str, total: int | None = None) -> dict:
    """Parse the one-line status plus the last controller-status TSV row.

    The status script has changed format during hardening, so accept ``key=value``, colon and
    whitespace separators and both hyphen/underscore spellings. Unknown text is ignored.
    """
    status_text, _, controller_text = text.partition("===CONTROLLER===")
    last: dict[str, str] = {}
    lines = [line for line in controller_text.splitlines() if line.strip()]
    if len(lines) >= 2:
        try:
            last = list(csv.DictReader(StringIO("\n".join(lines)), delimiter="\t"))[-1]
        except (csv.Error, IndexError):
            last = {}
    combined = status_text + "\n" + "\n".join(f"{key}={value}" for key, value in last.items())
    complete = _integer(combined, ("complete", "completed"))
    fraction = re.search(r"(?i)(\d+)\s*/\s*(\d+)\s*(?:complete|completed)", combined)
    if fraction:
        complete = complete if complete is not None else int(fraction.group(1))
        total = total or int(fraction.group(2))
    controller_job = ""
    job_match = re.search(r"(?i)(?:controller[-_ ]?job|job[-_ ]?id)\s*(?:=|:|\t| )\s*(\d+)", combined)
    if job_match:
        controller_job = job_match.group(1)
    failures = {}
    for failure_class in FAILURE_CLASSES:
        value = _integer(combined, (f"failed_{failure_class}", failure_class))
        if value is not None:
            failures[failure_class] = value
    return {
        "complete": complete or 0,
        "running": _integer(combined, ("running",)) or 0,
        "failed_by_class": failures,
        "never_submitted": _integer(combined, ("never_submitted", "unsubmitted")) or 0,
        "free_gib": _decimal(combined, ("free_gib", "free")),
        "controller_job": controller_job,
        "controller_state": str(last.get("state") or last.get("status") or ""),
        "total": total,
    }


def update_history(previous: dict | None, current: dict, limit: int = 96) -> list[dict]:
    history = list((previous or {}).get("history") or [])
    if previous and previous.get("timestamp"):
        old = {key: previous.get(key) for key in ("timestamp", "complete", "running", "total")}
        if not history or history[-1].get("timestamp") != old["timestamp"]:
            history.append(old)
    stamp = current.get("timestamp")
    history = [item for item in history if item.get("timestamp") != stamp]
    return history[-limit:]


def read_snapshot(project: Project, now: datetime | None = None) -> dict:
    path = project.rerun_snapshot
    if not path or not path.exists():
        return {"exists": False, "stale": True, "path": str(path or ""), "error": _pull_error(project)}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        stamp = datetime.fromisoformat(str(data["timestamp"]).replace("Z", "+00:00"))
    except (OSError, ValueError, KeyError):
        return {"exists": False, "stale": True, "path": str(path), "error": "invalid snapshot"}
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    age_hours = max(0.0, (now.astimezone(timezone.utc) - stamp.astimezone(timezone.utc)).total_seconds() / 3600)
    points = [*(data.get("history") or []), data]
    recent = []
    cutoff = now.astimezone(timezone.utc) - timedelta(hours=48)
    for point in points:
        try:
            when = datetime.fromisoformat(str(point["timestamp"]).replace("Z", "+00:00")).astimezone(timezone.utc)
            if when >= cutoff:
                recent.append((when, int(point.get("complete") or 0)))
        except (KeyError, TypeError, ValueError):
            continue
    recent.sort()
    rate = eta = None
    if len(recent) >= 2 and recent[-1][1] > recent[0][1]:
        hours = (recent[-1][0] - recent[0][0]).total_seconds() / 3600
        if hours > 0:
            rate = (recent[-1][1] - recent[0][1]) / hours
            remaining = max(0, int(data.get("total") or 0) - int(data.get("complete") or 0))
            eta = remaining / rate if rate else None
    data.update({
        "exists": True,
        "stale": age_hours > 24,
        "age_hours": round(age_hours, 1),
        "rate_per_hour": round(rate, 3) if rate is not None else None,
        "eta_hours": round(eta, 1) if eta is not None else None,
        "path": str(path),
        "error": _pull_error(project),
    })
    return data


def _pull_error(project: Project) -> str:
    path = project.rerun_pull_error
    if not path or not path.exists():
        return ""
    try:
        return str(json.loads(path.read_text(encoding="utf-8")).get("error") or "")[:300]
    except (OSError, ValueError):
        return "rerun pull failed"
