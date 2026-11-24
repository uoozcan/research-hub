"""Load hub.toml and answer path questions about it."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
WORKSPACE = Path(os.environ.get("RESEARCH_HUB_HOME") or (
    Path(os.environ["LOCALAPPDATA"]) / "ResearchHub" / "workspace"
    if os.environ.get("LOCALAPPDATA") else Path.home() / ".research-hub"
)).expanduser().resolve()
DATA = WORKSPACE / "data"

# Never listed, read or served, whatever the config says.
GUARDED = ("sealed_holdout", "prereg_snapshot")


@dataclass
class Project:
    key: str
    name: str
    tier: int
    color_slot: int
    repos: list[Path]
    vaults: list[Path]
    decisions: list[Path]
    reports: list[Path]
    non_git: list[Path]
    non_git_exclude: list[str]
    brain: Path | None
    figures: list[Path]
    workstreams: Path | None
    match: list[str]
    findings: list[Path] = field(default_factory=list)
    commitments: Path | None = None
    rerun_snapshot: Path | None = None
    rerun_pull_error: Path | None = None
    rerun_ssh_host: str | None = None
    rerun_remote_root: str | None = None
    rerun_total: int | None = None
    kanban_id: str | None = None
    aliases: list[str] = field(default_factory=list)
    read_only: bool = False

    def roots(self) -> list[Path]:
        out = [*self.repos, *self.vaults, *self.decisions, *self.reports, *self.non_git,
               *self.figures, *self.findings]
        if self.brain:
            out.append(self.brain)
        if self.commitments:
            out.append(self.commitments)
        if self.rerun_snapshot:
            out.append(self.rerun_snapshot)
        if self.rerun_pull_error:
            out.append(self.rerun_pull_error)
        return out


@dataclass
class LiteratureProfile:
    brief: str
    direct: list[str]
    context: list[str]
    require_context: bool = False


@dataclass
class LiteratureSettings:
    timezone: ZoneInfo = field(default_factory=lambda: ZoneInfo("UTC"))
    auto_limit: int = 3
    codex: str = ""
    filing_enabled: bool = False
    pubmed_query: str = ""
    arxiv_query: str = ""
    feeds: dict[str, str] = field(default_factory=dict)
    projects: dict[str, LiteratureProfile] = field(default_factory=dict)

    @property
    def configured(self) -> bool:
        return bool(self.pubmed_query or self.arxiv_query or self.feeds)


@dataclass
class Config:
    tz: ZoneInfo
    port: int
    claude_logs: list[Path]
    codex_sessions: list[Path]
    codex_imports: Path | None
    codex_index: list[Path]
    kanban: Path | None
    kanban_url: str
    cv_globs: list[str]
    cv_name_must_contain: list[str]
    skip_dirs: set[str]
    generic_cwds: set[str]
    projects: dict[str, Project]
    literature: LiteratureSettings = field(default_factory=LiteratureSettings)
    demo: bool = False

    def allowed_roots(self) -> list[Path]:
        roots = [r for p in self.projects.values() for r in p.roots()]
        roots += [Path(g.split("**")[0].rstrip("\\/")) for g in self.cv_globs]
        return roots


@lru_cache(maxsize=1)
def load(path: Path = WORKSPACE / "hub.toml") -> Config:
    source = path if path.is_file() else ROOT / "hub.example.toml"
    raw = tomllib.loads(source.read_text(encoding="utf-8-sig"))

    def location(value):
        target = Path(os.path.expandvars(value)).expanduser()
        return target if target.is_absolute() else path.parent / target

    def _paths(values):
        return [location(value) for value in (values or [])]

    h = raw["hub"]
    projects = {}
    for key, p in raw.get("projects", {}).items():
        projects[key] = Project(
            key=key, name=p["name"], tier=p.get("tier", 2), color_slot=p.get("color_slot", 1),
            repos=_paths(p.get("repos")), vaults=_paths(p.get("vaults")),
            decisions=_paths(p.get("decisions")), reports=_paths(p.get("reports")),
            non_git=_paths(p.get("non_git")), non_git_exclude=p.get("non_git_exclude", []),
            brain=location(p["brain"]) if p.get("brain") else None, figures=_paths(p.get("figures")),
            workstreams=location(p["workstreams"]) if p.get("workstreams") else None,
            match=[m.lower().replace("\\", "/") for m in p.get("match", [])],
            findings=_paths(p.get("findings")),
            commitments=location(p["commitments"]) if p.get("commitments") else None,
            rerun_snapshot=location(p["rerun_snapshot"]) if p.get("rerun_snapshot") else None,
            rerun_pull_error=location(p["rerun_pull_error"]) if p.get("rerun_pull_error") else None,
            rerun_ssh_host=p.get("rerun_ssh_host"),
            rerun_remote_root=p.get("rerun_remote_root"),
            rerun_total=p.get("rerun_total"),
            kanban_id=p.get("kanban_id"), aliases=p.get("aliases", []),
            read_only=p.get("read_only", False),
        )
    lit = raw.get("literature", {})
    literature = LiteratureSettings(
        timezone=ZoneInfo(lit.get("timezone", h.get("timezone", "UTC"))),
        auto_limit=max(0, int(lit.get("auto_limit", 3))),
        codex=lit.get("codex", "codex") if lit.get("synthesis_enabled", False) else "",
        filing_enabled=lit.get("filing_enabled", False),
        pubmed_query=lit.get("pubmed_query", ""), arxiv_query=lit.get("arxiv_query", ""),
        feeds=lit.get("feeds", {}),
        projects={key: LiteratureProfile(
            brief=profile["brief"], direct=profile.get("direct", []), context=profile.get("context", []),
            require_context=profile.get("require_context", False))
            for key, profile in lit.get("projects", {}).items() if key in projects and not projects[key].read_only},
    )
    return Config(
        tz=ZoneInfo(h.get("timezone", "UTC")), port=h.get("port", 8765),
        claude_logs=_paths(h.get("claude_logs")), codex_sessions=_paths(h.get("codex_sessions")),
        codex_imports=location(h["codex_imports"]) if h.get("codex_imports") else None,
        codex_index=_paths(h.get("codex_index")),
        kanban=location(h["kanban"]) if h.get("kanban") else None, kanban_url=h.get("kanban_url", ""),
        cv_globs=[str(location(value)) for value in h.get("cv_globs", [])],
        cv_name_must_contain=[x.lower() for x in h.get("cv_name_must_contain", [])], skip_dirs=set(h.get("skip_dirs", [])),
        generic_cwds={x.lower() for x in h.get("generic_cwds", [])}, projects=projects, literature=literature, demo=h.get("demo", False),
    )


def is_guarded(path: str | Path) -> bool:
    s = str(path).lower()
    return any(g in s for g in GUARDED)


def is_allowed(path: Path, cfg: Config) -> bool:
    """True if `path` sits under a configured root and is not a guarded record."""
    if is_guarded(path):
        return False
    try:
        p = path.resolve()
        if is_guarded(p):
            return False
    except (OSError, ValueError):
        return False
    for root in cfg.allowed_roots():
        try:
            if p.is_relative_to(root.resolve()):
                return True
        except OSError:
            continue
    return False
