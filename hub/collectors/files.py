"""File-modification activity in trees that are not git repos, aggregated per day and folder."""

from __future__ import annotations

import os
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from ..config import Config, is_guarded

MAX_EXAMPLES = 4


def collect(cfg: Config) -> tuple[list[dict], list[dict], list[str]]:
    events, notes = [], []
    for key, proj in cfg.projects.items():
        for root in proj.non_git:
            if not root.exists():
                notes.append(f"files: {root} not found")
                continue
            buckets: dict[tuple[str, str], list[str]] = defaultdict(list)
            n = 0
            for dirpath, dirnames, filenames in os.walk(root):
                rel_dir = os.path.relpath(dirpath, root).replace("\\", "/")
                # prune before descending: never list guarded or excluded folders
                keep = []
                for d in dirnames:
                    rel = f"{rel_dir}/{d}".lstrip("./")
                    if d in cfg.skip_dirs or d.startswith(".") or is_guarded(rel) \
                            or any(rel.startswith(x) for x in proj.non_git_exclude):
                        continue
                    keep.append(d)
                dirnames[:] = keep
                top = rel_dir.split("/")[0] if rel_dir != "." else "(top level)"
                for fn in filenames:
                    if fn.endswith(":Zone.Identifier") or fn.startswith("~$"):
                        continue
                    try:
                        mt = os.stat(os.path.join(dirpath, fn)).st_mtime
                    except OSError:
                        continue
                    day = datetime.fromtimestamp(mt, cfg.tz).strftime("%Y-%m-%d")
                    buckets[(day, top)].append(fn)
                    n += 1
            for (day, top), names in buckets.items():
                ex = ", ".join(sorted(names)[:MAX_EXAMPLES]) + (" …" if len(names) > MAX_EXAMPLES else "")
                events.append(dict(ts=f"{day}T12:00:00", day=day, project=key, source="file", kind="files",
                                   title=f"{len(names)} file{'s' if len(names) > 1 else ''} changed in "
                                         f"{root.name}/{top}", detail=ex, ref=str(Path(root) / top),
                                   weight=len(names)))
            notes.append(f"files: {root.name}: {n} files, {len(buckets)} day-folders")
    return events, [], notes
