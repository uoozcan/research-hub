"""FastAPI app: JSON API + static front end. Binds to 127.0.0.1 only (see __main__)."""

from __future__ import annotations

import glob
import json
import logging
import os
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal

import yaml
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import config, literature, overview_activity
from .accountability import read_commitments, read_decisions, read_findings, read_vault_staleness
from .rerun import read_snapshot
from .db import cache_get, cache_put, connect, file_stamp

ROOT = config.ROOT
app = FastAPI(title="Research Hub")


@app.middleware("http")
async def local_access(request: Request, call_next):
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = str(request.base_url).rstrip("/")
        if request.headers.get("origin") != origin:
            return JSONResponse({"detail": "Use the local Research Hub window for this operation"}, status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

IMG = {".png", ".jpg", ".jpeg", ".svg", ".gif", ".webp"}
FIG = IMG | {".pdf", ".html"}
PREFER = [".png", ".svg", ".jpg", ".jpeg", ".webp", ".gif", ".pdf", ".html"]


def cfg() -> config.Config:
    return config.load()


def _proj(key: str) -> config.Project:
    p = cfg().projects.get(key)
    if not p:
        raise HTTPException(404, f"unknown project {key}")
    return p


def _keys(project: str | None) -> list[str]:
    return list(cfg().projects) if not project or project == "all" else [_proj(project).key]


@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse((ROOT / "static" / "index.html").read_text(encoding="utf-8"), headers={
        "Cache-Control": "no-cache",
        "Content-Security-Policy": ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                                    "img-src 'self' data: blob:; frame-src 'self' about:; object-src 'self'; "
                                    "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"),
    })


@app.get("/api/health")
def health():
    return {"application": "research-hub", "instance": os.environ.get("RESEARCH_HUB_INSTANCE", "")}


@app.get("/api/projects")
def projects():
    c = cfg()
    with connect() as con:
        refreshed = con.execute("SELECT value FROM meta WHERE key='refreshed'").fetchone()
        last = {r["project"]: r["d"] for r in con.execute("SELECT project, MAX(day) d FROM events GROUP BY project")}
    return {"refreshed": refreshed["value"] if refreshed else None, "kanban_url": c.kanban_url,
            "projects": [dict(key=k, name=p.name, tier=p.tier, slot=p.color_slot, read_only=p.read_only,
                              has_brain=bool(p.brain and p.brain.exists()), has_vault=bool(p.vaults),
                              last_active=last.get(k)) for k, p in c.projects.items()]}


@app.get("/api/events")
def events(project: str = "all", start: str = "", end: str = "", source: str = ""):
    keys = _keys(project)
    q = f"SELECT * FROM events WHERE project IN ({','.join('?' * len(keys))})"
    args: list = list(keys)
    if start:
        q += " AND day >= ?"; args.append(start)
    if end:
        q += " AND day <= ?"; args.append(end)
    if source:
        q += " AND source = ?"; args.append(source)
    with connect() as con:
        rows = [dict(r) for r in con.execute(q + " ORDER BY ts", args)]
        pq = f"SELECT * FROM pomodoros WHERE project IN ({','.join('?' * len(keys))})"
        for r in con.execute(pq, keys):
            if (not start or r["day"] >= start) and (not end or r["day"] <= end) and source in ("", "pomodoro"):
                rows.append(dict(id=f"pomo{r['id']}", ts=r["started"], day=r["day"], project=r["project"],
                                 source="pomodoro", kind="pomodoro", title=f"Pomodoro {r['minutes']:.0f} min",
                                 detail=r["note"] or "", ref="", tool=None, weight=1))
    return rows


@app.get("/api/workstreams")
def workstreams(project: str = "all"):
    keys = _keys(project)
    with connect() as con:
        return [dict(r) for r in con.execute(
            f"SELECT * FROM workstreams WHERE project IN ({','.join('?' * len(keys))}) ORDER BY start", keys)]


@app.get("/api/accountability")
def accountability(project: str):
    p = _proj(project)
    return {"project": project, "findings": read_findings(p), "decisions": read_decisions(p),
            "commitments": read_commitments(p, datetime.now(cfg().tz).date()),
            "vaults": read_vault_staleness(p), "rerun": read_snapshot(p)}


@app.get("/api/activity")
def activity(project: str = "all"):
    """Per day: counts by source (git commits, AI sessions, file-days, decisions, pomodoros)."""
    out: dict[str, Counter] = defaultdict(Counter)
    for e in events(project):
        src = e["source"]
        out[e["day"]][src] += 1
    return [{"day": d, **c} for d, c in sorted(out.items())]


@app.get("/api/overview/activity")
def overview_activity_window(project: str = "all", days: int = 365):
    if days not in (90, 365):
        raise HTTPException(422, "Activity window must be 90 or 365 days")
    tz = cfg().tz
    return overview_activity.window(_keys(project), days, datetime.now(tz).date(), tz.key)


@app.get("/api/overview/activity/{day}")
def overview_activity_day(day: date, project: str = "all"):
    return overview_activity.day_detail(_keys(project), day, cfg())


def _week_key(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


@app.get("/api/progress")
def progress(project: str = "all", period: str = Query("week", pattern="^(day|week|month)$"), limit: int = 60):
    groups: dict[str, list[dict]] = defaultdict(list)
    for e in events(project):
        d = date.fromisoformat(e["day"])
        k = e["day"] if period == "day" else _week_key(d) if period == "week" else e["day"][:7]
        groups[k].append(e)
    summaries = _summaries(project, period)
    out = []
    for k in sorted(groups, reverse=True)[:limit]:
        evs = groups[k]
        c = Counter(e["source"] for e in evs)
        commits = [e["title"] for e in evs if e["kind"] == "commit"]
        highlights = [f"{e['kind']}: {e['title']}" for e in evs if e["kind"] in ("decision", "release", "report")]
        sessions = [f"{e['tool']}: {e['title']}" for e in evs if e["kind"] == "session"]
        handoffs = [dict(tool=e["tool"], title=e["title"], detail=e["detail"]) for e in evs if e["kind"] == "handoff"]
        out.append(dict(key=k, counts=dict(c), highlights=highlights[:12], commits=commits[:10],
                        n_commits=len(commits), sessions=sessions[:8], handoffs=handoffs,
                        projects=sorted({e["project"] for e in evs}), summary=summaries.get(k, "")))
    return out


def _summaries(project: str, period: str) -> dict[str, str]:
    """Narratives written by Claude/Codex: summaries/<project>/<period>/<key>.md."""
    out = {}
    for key in _keys(project):
        d = config.WORKSPACE / "summaries" / key / period
        for f in d.glob("*.md") if d.exists() else []:
            txt = f.read_text(encoding="utf-8").strip()
            out[f.stem] = (out.get(f.stem, "") + ("\n\n" if f.stem in out else "")
                           + (f"**{cfg().projects[key].name}.** " if project == "all" else "") + txt)
    return out


@app.get("/api/ideas")
def ideas(project: str = "all"):
    out = []
    for key in _keys(project):
        p = cfg().projects[key]
        files = [v / "ideas.yaml" for v in p.vaults] + [config.WORKSPACE / "ideas" / f"{key}.yaml"]
        for f in files:
            try:
                if not f.exists():
                    continue
                items = yaml.safe_load(f.read_text(encoding="utf-8")) or []
            except (OSError, yaml.YAMLError) as e:
                out.append(dict(id=f"err-{f}", title=f"Cannot read {f}: {e}", status="pending", project=key,
                                date=str(date.today()), lane="errors"))
                continue
            base = f.parent.parent if f.parent.name == ".ai-vault" else None
            for it in items:
                it = {k: (str(v) if isinstance(v, (date, datetime)) else v) for k, v in it.items()}
                it["project"] = key
                it["source_file"] = str(f)
                ev = it.get("evidence")
                if ev and base and not re.match(r"^[A-Za-z]:|^https?:", ev):
                    it["evidence_path"] = str((base / ev).resolve())
                elif ev and re.match(r"^[A-Za-z]:", ev):
                    it["evidence_path"] = ev
                out.append(it)
    return out


@app.get("/api/handoff")
def handoff(project: str):
    p = _proj(project)
    vaults = []
    for v in p.vaults:
        item = dict(path=str(v), exists=v.exists())
        if v.exists():
            for name in ("STATE.md", "HANDOFF.md", "traps.md"):
                f = v / name
                item[name] = f.read_text(encoding="utf-8") if f.exists() else ""
                if name == "HANDOFF.md" and f.exists():
                    item["handoff_mtime"] = datetime.fromtimestamp(f.stat().st_mtime, cfg().tz).isoformat()
            logs = sorted((v / "log").glob("*.md"), reverse=True)[:3] if (v / "log").exists() else []
            item["logs"] = [dict(day=f.stem, text=f.read_text(encoding="utf-8")) for f in logs]
            m = re.search(r"\*\*Tool:\*\*\s*(\w+)", item.get("HANDOFF.md", ""))
            item["last_tool"] = m.group(1) if m else None
        vaults.append(item)
    return vaults


@app.get("/api/figures")
def figures(project: str):
    p = _proj(project)
    groups: dict[tuple[str, str], dict] = {}
    for root in p.figures:
        if not root.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in cfg().skip_dirs and not config.is_guarded(d)]
            for fn in filenames:
                ext = Path(fn).suffix.lower()
                if ext not in FIG:
                    continue
                f = Path(dirpath) / fn
                g = groups.setdefault((dirpath, Path(fn).stem), dict(
                    stem=Path(fn).stem, folder=str(Path(dirpath).relative_to(root.parent)), files={}, mtime=0,
                    quarantined="quarantine" in dirpath.lower()))
                st = f.stat()
                g["files"][ext] = str(f)
                g["mtime"] = max(g["mtime"], st.st_mtime)
    out = []
    for g in groups.values():
        g["display"] = next(g["files"][e] for e in PREFER if e in g["files"])
        g["kind"] = Path(g["display"]).suffix.lower()
        g["date"] = datetime.fromtimestamp(g["mtime"], cfg().tz).strftime("%Y-%m-%d %H:%M")
        out.append(g)
    return sorted(out, key=lambda g: -g["mtime"])


@app.get("/api/file")
def file(path: str):
    f = Path(path)
    if not config.is_allowed(f, cfg()) or not f.is_file():
        raise HTTPException(403, "not an allowed file")
    headers = {"Content-Security-Policy": "sandbox"} if f.suffix.lower() in (".html", ".htm", ".svg") else {}
    return FileResponse(f, headers=headers)


@app.get("/api/kanban")
def kanban():
    c = cfg()
    if not c.kanban or not c.kanban.exists():
        return {"error": f"kanban data not found at {c.kanban}", "projects": []}
    data = json.loads(c.kanban.read_text(encoding="utf-8"))
    ids = {p.kanban_id: k for k, p in c.projects.items() if p.kanban_id}
    data["hub_keys"] = ids
    data["url"] = c.kanban_url
    return data


WIKILINK = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")


@app.get("/api/brain")
def brain(project: str):
    p = _proj(project)
    if not p.brain or not p.brain.exists():
        return {"root": str(p.brain), "notes": []}
    notes, links = [], defaultdict(list)
    for f in sorted(p.brain.rglob("*.md")):
        if any(part.startswith(".") for part in f.relative_to(p.brain).parts) or not config.is_allowed(f, cfg()):
            continue
        rel = f.relative_to(p.brain).as_posix()
        txt = f.read_text(encoding="utf-8", errors="replace")
        tags = sorted(set(re.findall(r"(?<![\w/])#([A-Za-z][\w/-]+)", txt)))
        for target in WIKILINK.findall(txt):
            links[target.strip().split("/")[-1].lower()].append(rel)
        notes.append(dict(path=rel, title=f.stem, folder=str(Path(rel).parent).replace("\\", "/"), tags=tags,
                          mtime=datetime.fromtimestamp(f.stat().st_mtime, cfg().tz).strftime("%Y-%m-%d")))
    for n in notes:
        n["backlinks"] = sorted(set(links.get(n["title"].lower(), [])) - {n["path"]})
    return {"root": str(p.brain), "notes": notes}


@app.get("/api/brain/note")
def brain_note(project: str, path: str):
    p = _proj(project)
    if not p.brain:
        raise HTTPException(404)
    f = (p.brain / path).resolve()
    if not f.is_relative_to(p.brain.resolve()) or config.is_guarded(f) or f.suffix != ".md" or not f.is_file():
        raise HTTPException(404)
    return {"path": path, "text": f.read_text(encoding="utf-8", errors="replace"),
            "obsidian": "obsidian://open?path=" + str(f).replace("\\", "/")}


@app.get("/api/cv")
def cv():
    files = []
    for g in cfg().cv_globs:
        for f in glob.glob(g, recursive=True):
            p = Path(f)
            must = cfg().cv_name_must_contain
            if p.name.startswith("~$") or (must and not any(m in p.name.lower() for m in must)):
                continue
            files.append(dict(path=str(p), name=p.name, folder=p.parent.name, ext=p.suffix.lower(),
                              mtime=datetime.fromtimestamp(p.stat().st_mtime, cfg().tz).strftime("%Y-%m-%d")))
    files.sort(key=lambda x: x["mtime"], reverse=True)
    return files


@app.get("/api/cv/html")
def cv_html(path: str = ""):
    import mammoth
    docs = [f for f in cv() if f["ext"] == ".docx"]
    target = Path(path) if path else (Path(docs[0]["path"]) if docs else None)
    if not target or target.suffix.lower() != ".docx" or not config.is_allowed(target, cfg()):
        raise HTTPException(404, "no CV .docx found")
    with connect() as con:
        key, stamp = f"cv:{target}", file_stamp(target)
        hit = cache_get(con, key, stamp)
        if hit is None:
            with target.open("rb") as fh:
                hit = mammoth.convert_to_html(fh).value
            cache_put(con, key, stamp, hit)
    return {"path": str(target), "html": hit}


class Pomodoro(BaseModel):
    project: str
    started: str
    ended: str
    minutes: float
    kind: str = "focus"
    note: str = ""


@app.post("/api/pomodoro")
def add_pomodoro(p: Pomodoro):
    _proj(p.project)
    day = datetime.fromisoformat(p.started.replace("Z", "+00:00")).astimezone(cfg().tz).strftime("%Y-%m-%d")
    with connect() as con:
        con.execute("INSERT INTO pomodoros (project, started, ended, day, minutes, kind, note) VALUES (?,?,?,?,?,?,?)",
                    (p.project, p.started, p.ended, day, p.minutes, p.kind, p.note[:300]))
    return {"ok": True, "day": day}


@app.get("/api/pomodoro/today")
def pomodoro_today():
    today = datetime.now(cfg().tz).strftime("%Y-%m-%d")
    with connect() as con:
        rows = [dict(r) for r in con.execute("SELECT * FROM pomodoros WHERE day=? ORDER BY started", (today,))]
    return {"day": today, "items": rows, "minutes": sum(r["minutes"] for r in rows)}


@app.get("/api/roadmap")
def roadmap():
    f = config.WORKSPACE / "reports" / "ROADMAP.md"
    if not f.is_file():
        f = f.with_name("WEBSITES_PLAN.md")
    return {"text": f.read_text(encoding="utf-8") if f.exists() else "", "path": str(f)}


@app.post("/api/refresh")
def refresh():
    if cfg().demo:
        from .demo import seed
        seed(refresh=True)
        return {"notes": ["total: offline demo index refreshed"]}
    from .refresh import run
    return {"notes": run(verbose=False)}




@app.get("/api/priorities")
def priorities(project: str = "all"):
    today = datetime.now(cfg().tz).date().isoformat()
    rows, unavailable = [], []

    def add(key, title, status, rank, path="", owner="", due="", detail=""):
        rows.append(dict(project=key, title=title, status=status, rank=rank, path=path,
                         owner=owner, due=due, detail=detail))

    for key in _keys(project):
        target = _proj(key)
        try:
            commitments = read_commitments(target, date.fromisoformat(today))
            for row in commitments["rows"]:
                if row["status"].casefold() in {"fixed", "closed", "done", "resolved", "completed", "cancelled", "delivered"}:
                    continue
                due = row["due"]
                overdue = bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", due) and due < today)
                add(key, row["item"], "Overdue" if overdue else row["status"], 0 if overdue else 4,
                    commitments["path"], row["owner"], due)
        except (OSError, ValueError, yaml.YAMLError):
            unavailable.append(f"{target.name} commitments")

        try:
            for row in read_findings(target)["rows"]:
                if row["status"].casefold() != "fixed" and row["severity"].upper() in {"FATAL", "MAJOR"}:
                    add(key, row["issue_id"], row["severity"].title(), 1 if row["severity"].upper() == "FATAL" else 2,
                        row["path"], row["owner"], detail=row["remaining_work"])
            for row in read_decisions(target)["rows"]:
                if row["awaiting_decision"]:
                    add(key, row["title"], "Decision needed", 3, row["path"])
        except (OSError, ValueError):
            unavailable.append(f"{target.name} findings or decisions")

        for vault in target.vaults:
            path = vault / "HANDOFF.md"
            if not path.is_file() or not config.is_allowed(path, cfg()):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                unavailable.append(f"{target.name} handoff")
                continue
            match = re.search(r"^## NEXT[ \t]*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
            if match and match[1].strip():
                content = match[1].strip()
                title = re.sub(r"^[-*]\s+", "", content.splitlines()[0]).strip()
                add(key, title[:170], "Next step", 5, str(path), detail=content[:320] if len(content.splitlines()) > 1 else "")

    rows.sort(key=lambda row: (row["rank"], row["due"] or "9999", row["project"], row["title"].casefold()))
    return {"items": rows[:24], "total": len(rows), "unavailable": unavailable}


class LiteratureDecision(BaseModel):
    project: str
    record: str
    action: Literal["save", "dismiss"]


class SavedIdea(BaseModel):
    day: date
    id: str


def _local_write(request: Request):
    hostname = request.url.hostname
    if hostname not in {"127.0.0.1", "localhost", "::1"} or request.headers.get("origin") != str(request.base_url).rstrip("/"):
        raise HTTPException(403, "Open the Research Hub locally to use this action")


@app.get("/api/literature")
def literature_view(project: str = "all", day: date | None = None):
    target = _proj(project).key if project != "all" else None
    return literature.view(config.DATA, cfg(), day=day.isoformat() if day else None, project=target)


def _literature_background(project=None, run=False):
    try:
        if run:
            literature.run(cfg(), force=True)
        else:
            literature.retry(cfg(), project)
    except literature.Busy:
        return
    except Exception as exc:
        logging.getLogger("uvicorn.error").error("Literature operation failed: %s", type(exc).__name__)


@app.post("/api/literature/run", status_code=202)
def literature_run(request: Request, background: BackgroundTasks):
    _local_write(request)
    if not cfg().literature.configured:
        raise HTTPException(409, "Configure at least one literature source in hub.toml")
    background.add_task(_literature_background, run=True)
    return {"accepted": True}


@app.post("/api/literature/retry", status_code=202)
def literature_retry(request: Request, background: BackgroundTasks, project: str = "all"):
    _local_write(request)
    if not cfg().literature.filing_enabled:
        raise HTTPException(409, "Enable filing in hub.toml before retrying")
    target = _proj(project).key if project != "all" else None
    background.add_task(_literature_background, project=target)
    return {"accepted": True}


@app.post("/api/literature/decision")
def literature_decision(request: Request, body: LiteratureDecision, background: BackgroundTasks):
    _local_write(request)
    _proj(body.project)
    if body.action == "save" and not cfg().literature.filing_enabled:
        raise HTTPException(409, "Enable filing in hub.toml before saving")
    try:
        result = literature.decide(cfg(), body.project, body.record, body.action, flush=False)
        if body.action == "save":
            background.add_task(_literature_background, project=body.project)
        return result
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except literature.Busy as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/literature/ideas")
def literature_idea(request: Request, body: SavedIdea):
    _local_write(request)
    try:
        literature.save_idea(config.DATA, body.day.isoformat(), body.id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"saved": True}


@app.get("/api/literature/ris")
def literature_ris(project: str):
    key = _proj(project).key
    return Response(literature.export_ris(config.DATA, key), media_type="application/x-research-info-systems",
                    headers={"Content-Disposition": f'attachment; filename="research-hub-{key}.ris"'})


@app.exception_handler(Exception)
def on_error(_, exc: Exception):
    return JSONResponse({"error": f"{type(exc).__name__}: {exc}"}, status_code=500)
