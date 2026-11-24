"""Commits, release tags and branch segments from each project repo."""

from __future__ import annotations

import subprocess
from pathlib import Path

from ..classify import to_local
from ..config import Config

SEP = "\x1f"


def _git(repo: Path, *args: str) -> str:
    r = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if r.returncode:
        raise RuntimeError(r.stderr.strip()[:300])
    return r.stdout


def collect(cfg: Config) -> tuple[list[dict], list[dict], list[str]]:
    events, streams, notes = [], [], []
    for key, proj in cfg.projects.items():
        # The same commit can appear in multiple configured repositories or mirrors.
        # Count each once: by SHA, then by (author date, subject).
        seen_sha, seen_subj = set(), set()
        for repo in proj.repos:
            if not (repo / ".git").exists():
                notes.append(f"git: {repo} not found or not a repo")
                continue
            label = repo.name
            try:
                log = _git(repo, "log", "--all", f"--format=%H{SEP}%aI{SEP}%s{SEP}%D")
            except (RuntimeError, OSError) as e:
                notes.append(f"git: {repo}: {e}")
                continue
            n = dup = 0
            for line in log.splitlines():
                parts = line.split(SEP)
                if len(parts) < 4:
                    continue
                sha, ts, subject, refs = parts
                loc = to_local(ts, cfg)
                if not loc:
                    continue
                if sha in seen_sha or (ts, subject) in seen_subj:
                    dup += 1
                    continue
                seen_sha.add(sha)
                seen_subj.add((ts, subject))
                n += 1
                events.append(dict(ts=loc[0], day=loc[1], project=key, source="git", kind="commit",
                                   title=subject[:160], detail=label, ref=f"{label}@{sha[:9]}"))
                for ref in refs.split(", "):
                    if ref.startswith("tag: "):
                        tag = ref[5:]
                        events.append(dict(ts=loc[0], day=loc[1], project=key, source="git",
                                           kind="release", title=f"{label} {tag}", detail=subject[:160],
                                           ref=f"{label}@{tag}"))
            notes.append(f"git: {label}: {n} commits" + (f" ({dup} duplicates skipped)" if dup else ""))
            streams += _branch_segments(repo, key, label, cfg)
    return events, streams, notes


def _branch_segments(repo: Path, key: str, label: str, cfg: Config) -> list[dict]:
    """One bar per local branch: its commits since the nearest branch tip or release tag below it.

    Branch tips and tags that are strict ancestors of the branch are the boundaries, so a branch
    merged into a later one still gets only its own stretch of work, not the whole history.
    """
    out = []
    current = _git(repo, "branch", "--show-current").strip()
    for b in _git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads").split():
        tip = _git(repo, "rev-parse", f"{b}^{{commit}}").strip()
        below = []
        for line in _git(repo, "for-each-ref", "--merged", b, "--format=%(objectname) %(*objectname) %(refname)",
                         "refs/heads", "refs/tags").splitlines():
            parts = line.split()
            obj, ref = (parts[1] if len(parts) == 3 else parts[0]), parts[-1]
            if obj != tip:
                below.append(ref)
        dates = [d for d in _git(repo, "log", b, "--format=%aI", *(["--not", *below] if below else [])).split() if d]
        if not dates:
            continue
        days = sorted(to_local(d, cfg)[1] for d in dates)
        title = f"{label}: {b}" if below else f"{label}: history up to {b}"
        out.append(dict(project=key, title=title, start=days[0], end=days[-1],
                        status="active" if b == current else "done",
                        source="git", ref=f"{label}@{b}", detail=f"{len(dates)} commits since the previous branch point or release"))
    return out
