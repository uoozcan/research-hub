"""Regression tests for the parts of the hub that have already gone wrong once.

Run: .venv\\Scripts\\python -m pytest -q
"""

import json
import subprocess
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from hub import config
from hub.classify import classify, to_local
from hub.collectors import ai_logs, git as git_collector
from hub.accountability import read_commitments, read_decisions, read_findings, read_vault_staleness
from hub.rerun import parse_remote_output, read_snapshot, update_history


def make_cfg(tmp_path: Path, **over) -> config.Config:
    def proj(key, match, **kw):
        base = dict(key=key, name=key, tier=1, color_slot=1, repos=[], vaults=[], decisions=[], reports=[],
                    non_git=[], non_git_exclude=[], brain=None, figures=[], workstreams=None, match=match)
        base.update(kw)
        return config.Project(**base)
    projects = over.pop("projects", None) or {
        "atlas": proj("atlas", ["shared_workspace", "atlas-tools", "atlas"]),
        "secondary": proj("secondary", ["secondary"]),
        "screening": proj("screening", ["projects/screening", "response-browser"]),
        "variants": proj("variants", ["variants-profiler", "workspaces/screening"]),
        "other": proj("other", ["kanban-boards"]),
    }
    cfg = config.Config(tz=ZoneInfo("Europe/Helsinki"), port=0, claude_logs=[], codex_sessions=[],
                        codex_imports=None, codex_index=[], kanban=None, kanban_url="", cv_globs=[],
                        cv_name_must_contain=[], skip_dirs=set(), generic_cwds={"shared_workspace", "downloads"},
                        projects=projects)
    for k, v in over.items():
        setattr(cfg, k, v)
    return cfg


# ---------- attribution ----------

def test_deepest_match_wins(tmp_path):
    cfg = make_cfg(tmp_path)
    assert classify("/home/example/projects/shared_workspace/secondary_na/run.py", cfg) == "secondary"
    assert classify("/home/example/projects/shared_workspace/kanban-boards/data.json", cfg) == "other"
    assert classify(r"D:\projects\shared_workspace\atlas_publication_candidate\x.md", cfg) == "atlas"


def test_variants_is_not_screening(tmp_path):
    cfg = make_cfg(tmp_path)
    assert classify(r"D:\Users\example\Documents\Workspaces\screening\src\cli.py", cfg) == "variants"
    assert classify(r"D:\projects\variants-profiler\README.md", cfg) == "variants"
    assert classify(r"D:\projects\screening\docs\x.md", cfg) == "screening"


def test_memory_and_tool_folders_are_ignored(tmp_path):
    cfg = make_cfg(tmp_path)
    # memory files are named after projects but are not project work
    assert classify(r"C:\Users\example\.claude\projects\d--Users\memory\atlas_x.md", cfg) is None
    assert classify("cat ~/.codex/sessions/atlas.jsonl", cfg) is None


def test_generic_cwd_gives_no_default_project(tmp_path):
    cfg = make_cfg(tmp_path)
    assert ai_logs._cwd_project("/home/example/projects/shared_workspace", cfg) is None
    assert ai_logs._cwd_project(r"D:\Users\example\Downloads", cfg) is None
    assert ai_logs._cwd_project("/home/example/projects/shared_workspace/kanban-boards", cfg) == "other"


def test_local_day_uses_helsinki(tmp_path):
    cfg = make_cfg(tmp_path)
    # 23:30 UTC on the 6th is already the 7th in Helsinki
    assert to_local("2026-10-06T23:30:00Z", cfg)[1] == "2026-10-07"


# ---------- seal guard ----------

def test_guarded_paths_are_never_allowed(tmp_path):
    root = tmp_path / "proj"
    (root / "runs" / "sealed_holdout").mkdir(parents=True)
    f = root / "runs" / "sealed_holdout" / "x.tsv"
    f.write_text("secret")
    ok = root / "fig.png"
    ok.write_text("png")
    cfg = make_cfg(tmp_path)
    cfg.projects["atlas"].figures = [root]
    assert config.is_allowed(ok, cfg)
    assert not config.is_allowed(f, cfg)
    assert not config.is_allowed(tmp_path / "elsewhere.txt", cfg)


# ---------- accountability ----------

def test_findings_collector_normalizes_status_and_counts(tmp_path):
    ledger = tmp_path / "closure.tsv"
    ledger.write_text(
        "issue_id\tsource\tseverity\tcurrent_status\tremaining_work\towner\tlast_verified\n"
        "SR-001\treview\tFATAL\tfixed\tretain\tanalysis\t2026-10-13\n"
        "SR-002\treview\tMAJOR\tpartial\trerun\tcluster\t2026-10-13\n"
        "PSR-001\treview\tFATAL\texternal blocker\tpurge\tauthor\t2026-10-13\n",
        encoding="utf-8",
    )
    cfg = make_cfg(tmp_path)
    project = cfg.projects["atlas"]
    project.findings = [ledger]
    found = read_findings(project)
    assert found["summary"]["total"] == 3
    assert found["summary"]["non_fixed"] == 2
    assert found["summary"]["by_status"] == {"external blocker": 1, "fixed": 1, "partial": 1}
    assert found["rows"][1]["owner"] == "cluster"


def test_decision_ledger_flags_only_proposed_and_draft(tmp_path):
    decisions = tmp_path / "decisions"
    decisions.mkdir()
    (decisions / "20261013_signed.json").write_text(
        json.dumps({"date": "2026-10-13", "decision_id": "signed", "status": "SIGNED"}),
        encoding="utf-8",
    )
    (decisions / "20261014_proposal.json").write_text(
        json.dumps({"date": "2026-10-14", "title": "RNA route", "status": "PROPOSED - sign me"}),
        encoding="utf-8",
    )
    (decisions / "20261015_draft.json").write_text(
        json.dumps({"decision_id": "20261015_draft", "status": "DRAFT - incomplete"}),
        encoding="utf-8",
    )
    cfg = make_cfg(tmp_path)
    project = cfg.projects["atlas"]
    project.decisions = [decisions]
    result = read_decisions(project)
    assert result["summary"] == {"total": 3, "awaiting_decision": 2}
    assert [row["title"] for row in result["rows"] if row["awaiting_decision"]] == ["RNA route", "Draft"]


def test_commitments_are_sorted_by_age_and_counted(tmp_path):
    from datetime import date

    commitments = tmp_path / "commitments.yaml"
    commitments.write_text(
        "- item: Newer\n  owner: CSC\n  opened: 2026-10-15\n  status: blocked\n  evidence: cert\n"
        "- item: Older\n  owner: author\n  opened: 2026-10-08\n  status: waiting\n  evidence: review\n"
        "- item: Closed\n  owner: author\n  opened: 2026-10-01\n  status: done\n  evidence: commit\n",
        encoding="utf-8",
    )
    cfg = make_cfg(tmp_path)
    project = cfg.projects["atlas"]
    project.commitments = commitments
    result = read_commitments(project, date(2026, 10, 16))
    assert result["summary"] == {"total": 3, "open": 2}
    assert [row["item"] for row in result["rows"]] == ["Closed", "Older", "Newer"]
    assert result["rows"][1]["age_days"] == 8


def test_vault_is_stale_when_state_predates_newest_commit(tmp_path):
    import os
    import time

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "tracked.txt").write_text("one", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "new")
    commit_ts = int(subprocess.run(
        ["git", "-C", str(repo), "log", "-1", "--format=%ct"], check=True,
        capture_output=True, text=True,
    ).stdout.strip())
    vault = repo / ".ai-vault"
    vault.mkdir()
    state = vault / "STATE.md"
    state.write_text("old", encoding="utf-8")
    os.utime(state, (commit_ts - 60, commit_ts - 60))
    cfg = make_cfg(tmp_path)
    project = cfg.projects["atlas"]
    project.repos = [repo]
    project.vaults = [vault]
    result = read_vault_staleness(project)
    assert result["summary"] == {"total": 1, "stale": 1}
    os.utime(state, (time.time() + 60, time.time() + 60))
    assert read_vault_staleness(project)["summary"]["stale"] == 0


def test_rerun_status_parser_keeps_aggregate_counts_only():
    text = (
        "complete=52 running=2 never_submitted=177 failed_storage=3 "
        "failed_input_transfer=2 free_gib=41.5 controller_job=2339999\n"
        "===CONTROLLER===\n"
        "timestamp\tjob_id\tstate\n2026-10-16T10:00:00Z\t2339999\tRUNNING\n"
    )
    result = parse_remote_output(text, total=236)
    assert result == {
        "complete": 52, "running": 2,
        "failed_by_class": {"storage": 3, "input_transfer": 2},
        "never_submitted": 177, "free_gib": 41.5,
        "controller_job": "2339999", "controller_state": "RUNNING", "total": 236,
    }
    assert "subject" not in result


def test_rerun_snapshot_staleness_and_eta(tmp_path):
    from datetime import datetime, timezone

    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(json.dumps({
        "timestamp": "2026-10-16T10:00:00Z", "complete": 52, "running": 2, "total": 236,
        "failed_by_class": {}, "never_submitted": 182, "free_gib": 40,
        "controller_job": "1", "history": [
            {"timestamp": "2026-10-16T04:00:00Z", "complete": 46, "running": 2, "total": 236}
        ],
    }), encoding="utf-8")
    cfg = make_cfg(tmp_path)
    project = cfg.projects["atlas"]
    project.rerun_snapshot = snapshot
    result = read_snapshot(project, datetime(2026, 10, 16, 12, tzinfo=timezone.utc))
    assert not result["stale"]
    assert result["rate_per_hour"] == 1.0
    assert result["eta_hours"] == 184.0
    stale = read_snapshot(project, datetime(2026, 10, 17, 12, 1, tzinfo=timezone.utc))
    assert stale["stale"]


def test_rerun_history_does_not_duplicate_previous_snapshot():
    previous = {"timestamp": "2026-10-16T10:00:00Z", "complete": 4, "running": 1, "total": 10,
                "history": [{"timestamp": "2026-10-16T09:00:00Z", "complete": 3}]}
    current = {"timestamp": "2026-10-16T11:00:00Z", "complete": 5}
    history = update_history(previous, current)
    assert [row["complete"] for row in history] == [3, 4]


# ---------- git dedupe ----------

def _git(repo, *a):
    subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)


def test_commits_shared_between_repos_count_once(tmp_path):
    a, b = tmp_path / "paper", tmp_path / "pipeline"
    a.mkdir()
    _git(a, "init", "-q", "-b", "main")
    for i in range(3):
        (a / f"f{i}").write_text(str(i))
        _git(a, "add", "-A")
        _git(a, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", f"c{i}")
    subprocess.run(["git", "clone", "-q", str(a), str(b)], check=True)
    cfg = make_cfg(tmp_path)
    cfg.projects["atlas"].repos = [a, b]
    events, streams, notes = git_collector.collect(cfg)
    commits = [e for e in events if e["kind"] == "commit"]
    assert len(commits) == 3, notes
    assert any("duplicates skipped" in n for n in notes)


# ---------- codex imports ----------

def _rollout(path: Path, sid: str, cwd: str, thread_source=None, calls=("echo hi",)):
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = {"id": sid, "cwd": cwd}
    if thread_source is not None:
        meta["thread_source"] = thread_source
    lines = [{"timestamp": "2026-10-10T10:00:00Z", "type": "session_meta", "payload": meta}]
    for c in calls:
        lines.append({"timestamp": "2026-10-10T10:01:00Z", "type": "response_item",
                      "payload": {"type": "function_call", "arguments": json.dumps({"command": c})}})
    path.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")


def test_codex_imports_and_subagents(tmp_path, monkeypatch):
    sessions = tmp_path / "sessions"
    still_there = tmp_path / "claude_original.jsonl"
    still_there.write_text("{}")
    _rollout(sessions / "a.jsonl", "native", r"D:\projects\screening", "user")
    _rollout(sessions / "b.jsonl", "old-wsl-no-thread-source", "/home/example/projects/screening")
    _rollout(sessions / "c.jsonl", "imp-dup", "/x", None, calls=("cat D:/projects/screening/a",))
    _rollout(sessions / "d.jsonl", "imp-only-copy", "/x", None, calls=("cat D:/projects/screening/b",))
    _rollout(sessions / "e.jsonl", "guardian", r"D:\projects\screening", "guardian_review")
    imports = tmp_path / "imports.json"
    imports.write_text(json.dumps({"records": [
        {"imported_thread_id": "imp-dup", "source_path": str(still_there), "title": "dup"},
        {"imported_thread_id": "imp-only-copy", "source_path": str(tmp_path / "gone.jsonl"), "title": "only copy"},
    ]}))
    cfg = make_cfg(tmp_path, codex_sessions=[sessions], codex_imports=imports)

    class FakeCon:
        def execute(self, *a, **k):
            class R:
                def fetchone(self):
                    return None
            return R()
    monkeypatch.setattr(ai_logs, "cache_get", lambda *a: None)
    monkeypatch.setattr(ai_logs, "cache_put", lambda *a: None)
    events, _, notes = ai_logs.collect(cfg, FakeCon())
    by_ref = {e["ref"]: e for e in events}
    assert "native" in by_ref and by_ref["native"]["tool"] == "codex"
    assert "old-wsl-no-thread-source" in by_ref          # missing thread_source is NOT an import
    assert "imp-dup" not in by_ref                       # Claude original exists -> skipped
    assert by_ref["imp-only-copy"]["tool"] == "claude"   # only surviving copy -> counted as Claude
    assert "guardian" not in by_ref                      # subagents are not sessions
