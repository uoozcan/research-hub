"""Public assets and private user workspaces have independent lifecycles."""

from __future__ import annotations

from contextlib import closing
import re
import shutil
import sqlite3
import tempfile
from pathlib import Path

from . import config

PROFILE = "hub.toml"
DATABASES = ("hub.sqlite", "literature.sqlite")
DOCUMENTS = {"summaries": {".md"}, "ideas": {".yaml", ".yml"},
             "reports": {".md", ".tsv", ".json"}, "workstreams": {".yaml", ".yml"}}


def prepare():
    path = config.WORKSPACE / PROFILE
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("xb") as output:
                output.write((config.ROOT / "hub.example.toml").read_bytes())
        except FileExistsError:
            pass
    if config.load().demo:
        from .demo import seed

        seed()


def _copy_documents(source, destination):
    for name, extensions in DOCUMENTS.items():
        for candidate in (source / name).rglob("*"):
            if (candidate.suffix.lower() not in extensions or not candidate.is_file()
                    or config.is_guarded(candidate.resolve()) or not candidate.resolve().is_relative_to(source)):
                continue
            target = destination / candidate.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(candidate, target)


def _profile(source):
    content = (source / PROFILE).read_text(encoding="utf-8-sig")
    spellings = (str(source), source.as_posix(), str(source).replace("\\", "\\\\"))
    for path in sorted(set(spellings), key=len, reverse=True):
        content = re.sub(re.escape(path) + r"(?=[\\/])", ".", content, flags=re.I)
    return content


def import_workspace(source: Path, destination: Path):
    source, destination = source.resolve(), destination.resolve()
    if destination.exists() or destination.is_relative_to(source):
        raise ValueError("Choose a new workspace folder outside the source")
    if not (source / PROFILE).is_file():
        raise ValueError("The source workspace has no hub.toml")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".research-hub-import-", dir=destination.parent) as stopover:
        staged = Path(stopover) / "workspace"
        (staged / "data").mkdir(parents=True)
        profile = _profile(source)
        # Reconnecting write-capable integrations is an explicit action in the copied workspace.
        profile = re.sub(r"(?m)^(\s*(?:synthesis_enabled|filing_enabled)\s*=\s*)true\b", r"\g<1>false", profile)
        (staged / PROFILE).write_text(profile, encoding="utf-8", newline="\n")
        for name in DATABASES:
            database = source / "data" / name
            if database.is_file() and database.resolve().is_relative_to(source):
                with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as live:
                    with closing(sqlite3.connect(staged / "data" / name)) as copied:
                        live.backup(copied)
        for pattern in ("*_snapshot.json", "*_pull_error.json"):
            for candidate in (source / "data").glob(pattern):
                if candidate.is_file() and candidate.resolve().is_relative_to(source) and not config.is_guarded(candidate.resolve()):
                    shutil.copyfile(candidate, staged / "data" / candidate.name)
        _copy_documents(source, staged)
        config.load(staged / PROFILE)
        config.load.cache_clear()
        staged.rename(destination)
