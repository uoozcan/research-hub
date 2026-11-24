"""Read configured findings and accountability records without copying sensitive data."""

from __future__ import annotations

import csv
import json
import re
import subprocess
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import yaml

from .config import Project


FIXED = {"fixed", "closed", "done", "resolved"}
DATE_RE = re.compile(r"(20\d{2})[-_]?(\d{2})[-_]?(\d{2})")


def read_findings(project: Project) -> dict:
    """Return normalized finding rows plus status/severity counts.

    Review ledgers vary between ``status`` and ``current_status``. Unknown columns are retained
    in ``detail`` so adding a project-specific ledger does not require code changes.
    """
    rows: list[dict] = []
    for path in project.findings:
        if not path.exists() or path.suffix.lower() not in {".tsv", ".csv"}:
            continue
        delimiter = "\t" if path.suffix.lower() == ".tsv" else ","
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for raw in csv.DictReader(handle, delimiter=delimiter):
                issue_id = (raw.get("issue_id") or raw.get("id") or "").strip()
                if not issue_id:
                    continue
                status = (raw.get("current_status") or raw.get("status") or "unknown").strip()
                severity = (raw.get("severity") or "unspecified").strip()
                owner = (raw.get("owner") or "unassigned").strip()
                row = {
                    "issue_id": issue_id,
                    "source": (raw.get("source") or path.stem).strip(),
                    "severity": severity,
                    "status": status,
                    "owner": owner,
                    "evidence": (raw.get("evidence") or "").strip(),
                    "remaining_work": (raw.get("remaining_work") or "").strip(),
                    "last_verified": (raw.get("last_verified") or "").strip(),
                    "path": str(path),
                }
                rows.append(row)
    by_status = Counter(r["status"] for r in rows)
    by_status_severity = Counter((r["status"], r["severity"]) for r in rows)
    non_fixed = [r for r in rows if r["status"].lower() not in FIXED]
    return {
        "rows": rows,
        "summary": {
            "total": len(rows),
            "non_fixed": len(non_fixed),
            "by_status": dict(sorted(by_status.items())),
            "by_status_severity": [
                {"status": status, "severity": severity, "count": count}
                for (status, severity), count in sorted(by_status_severity.items())
            ],
        },
    }


def _decision_title(path: Path, record: dict) -> str:
    title = record.get("title") or record.get("decision_id") or path.stem
    if not isinstance(title, str):
        title = path.stem
    title = DATE_RE.sub("", title).strip("_- ").replace("_", " ").replace("-", " ")
    return title[:1].upper() + title[1:] if title else path.stem


def read_decisions(project: Project) -> dict:
    rows: list[dict] = []
    for directory in project.decisions:
        for path in sorted(directory.glob("*.json")) if directory.exists() else []:
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            raw_status = record.get("status")
            if raw_status is None and "signed" in record:
                raw_status = "SIGNED" if record.get("signed") else "UNSIGNED"
            status = str(raw_status or "not stated")
            upper = status.upper().strip()
            awaiting = upper.startswith("PROPOSED") or upper.startswith("DRAFT")
            match = DATE_RE.search(path.name)
            date_value = record.get("date") if isinstance(record.get("date"), str) else None
            if not date_value and match:
                date_value = "-".join(match.groups())
            rows.append({
                "date": date_value or "",
                "title": _decision_title(path, record),
                "status": status,
                "awaiting_decision": awaiting,
                "path": str(path),
            })
    rows.sort(key=lambda row: (row["date"], row["title"]))
    return {
        "rows": rows,
        "summary": {
            "total": len(rows),
            "awaiting_decision": sum(row["awaiting_decision"] for row in rows),
        },
    }


def read_commitments(project: Project, today: date | None = None) -> dict:
    path = project.commitments
    if not path or not path.exists():
        return {"rows": [], "summary": {"total": 0, "open": 0}, "path": str(path or "")}
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    except (OSError, yaml.YAMLError):
        payload = []
    today = today or date.today()
    rows = []
    for raw in payload if isinstance(payload, list) else []:
        if not isinstance(raw, dict) or not raw.get("item"):
            continue
        opened = raw.get("opened")
        if isinstance(opened, datetime):
            opened = opened.date()
        if isinstance(opened, str):
            try:
                opened = date.fromisoformat(opened)
            except ValueError:
                opened = None
        status = str(raw.get("status") or "waiting")
        rows.append({
            "item": str(raw["item"]),
            "owner": str(raw.get("owner") or "unassigned"),
            "opened": opened.isoformat() if isinstance(opened, date) else str(raw.get("opened") or ""),
            "due": str(raw.get("due") or ""),
            "status": status,
            "evidence": str(raw.get("evidence") or ""),
            "age_days": (today - opened).days if isinstance(opened, date) else None,
        })
    rows.sort(key=lambda row: (-(row["age_days"] if row["age_days"] is not None else -1), row["item"]))
    open_count = sum(row["status"].lower() not in FIXED for row in rows)
    return {"rows": rows, "summary": {"total": len(rows), "open": open_count}, "path": str(path)}


def read_vault_staleness(project: Project) -> dict:
    rows = []
    for index, vault in enumerate(project.vaults):
        repo = vault.parent if vault.parent in project.repos else (
            project.repos[index] if index < len(project.repos) else vault.parent
        )
        state = vault / "STATE.md"
        commit_ts = None
        if (repo / ".git").exists():
            result = subprocess.run(
                ["git", "-c", "safe.directory=*", "-C", str(repo), "log", "--all", "-1", "--format=%ct"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            try:
                commit_ts = int(result.stdout.strip()) if result.returncode == 0 else None
            except ValueError:
                commit_ts = None
        state_ts = int(state.stat().st_mtime) if state.exists() else None
        stale = state_ts is None or (commit_ts is not None and state_ts < commit_ts)
        rows.append({
            "vault": str(vault),
            "repo": str(repo),
            "state_path": str(state),
            "state_exists": state.exists(),
            "state_updated": datetime.fromtimestamp(state_ts).isoformat(timespec="minutes") if state_ts else "",
            "newest_commit": datetime.fromtimestamp(commit_ts).isoformat(timespec="minutes") if commit_ts else "",
            "stale": stale,
        })
    return {"rows": rows, "summary": {"total": len(rows), "stale": sum(row["stale"] for row in rows)}}
