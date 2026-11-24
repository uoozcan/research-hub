"""Run every collector and rebuild the events and workstreams tables."""

from __future__ import annotations

import time
from collections import Counter
from datetime import datetime

from . import config
from .collectors import ai_logs, files, git, records
from .db import connect, event_id


def run(verbose: bool = True) -> list[str]:
    cfg = config.load()
    t0 = time.time()
    events, streams, notes = [], [], []
    with connect() as con:
        for name, fn in (("git", lambda: git.collect(cfg)), ("records", lambda: records.collect(cfg)),
                         ("files", lambda: files.collect(cfg)), ("ai", lambda: ai_logs.collect(cfg, con))):
            t = time.time()
            try:
                e, s, n = fn()
            except Exception as exc:  # one broken source must not blank the hub
                e, s, n = [], [], [f"{name}: FAILED {type(exc).__name__}: {exc}"]
            events += e
            streams += s
            notes += n + [f"{name}: {time.time() - t:.1f}s"]
        con.execute("DELETE FROM events")
        con.execute("DELETE FROM workstreams")
        seen = set()
        for e in events:
            eid = event_id(e["source"], e["kind"], e.get("ref", ""), e["day"], e["project"], e["title"][:60])
            if eid in seen:
                continue
            seen.add(eid)
            con.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (eid, e["ts"], e["day"], e["project"], e["source"], e["kind"], e["title"],
                         e.get("detail", ""), e.get("ref", ""), e.get("tool"), int(e.get("weight", 1))))
        for s in streams:
            sid = event_id("ws", s["project"], s["title"], s["start"])
            con.execute("INSERT OR REPLACE INTO workstreams VALUES (?,?,?,?,?,?,?,?,?)",
                        (sid, s["project"], s["title"], s["start"], s["end"], s["status"], s["source"],
                         s.get("ref", ""), s.get("detail", "")))
        con.execute("INSERT OR REPLACE INTO meta VALUES ('refreshed', ?)", (datetime.now(cfg.tz).isoformat(),))
        counts = Counter((r["project"], r["source"]) for r in
                         con.execute("SELECT project, source FROM events").fetchall())
    notes.append(f"total: {len(seen)} events, {len(streams)} workstreams in {time.time() - t0:.1f}s")
    for (p, s), n in sorted(counts.items()):
        notes.append(f"  {p:18s} {s:9s} {n}")
    if verbose:
        print("\n".join(notes))
    return notes
