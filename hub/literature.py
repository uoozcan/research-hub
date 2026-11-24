"""Scheduled literature runs, report history, and per-project review state."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import sqlite3
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from . import config
from .literature_ai import SynthError, synthesize
from .literature_destinations import DestinationError, Zotero, ris, save_note
from .literature_sources import PublicHTTP, Record, SourceError, source_jobs

SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    id TEXT PRIMARY KEY, doi TEXT, pmid TEXT, arxiv TEXT, payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS records_doi ON records(doi);
CREATE INDEX IF NOT EXISTS records_pmid ON records(pmid);
CREATE INDEX IF NOT EXISTS records_arxiv ON records(arxiv);
CREATE TABLE IF NOT EXISTS seen (
    day TEXT NOT NULL, record TEXT NOT NULL REFERENCES records(id), PRIMARY KEY(day, record)
);
CREATE TABLE IF NOT EXISTS sources (
    name TEXT PRIMARY KEY, checked TEXT NOT NULL, checkpoint TEXT, status TEXT NOT NULL, detail TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reports (
    day TEXT PRIMARY KEY, state TEXT NOT NULL, updated TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS filings (
    project TEXT NOT NULL, record TEXT NOT NULL REFERENCES records(id), day TEXT NOT NULL,
    decision TEXT NOT NULL, reason TEXT NOT NULL, automatic INTEGER NOT NULL,
    note TEXT NOT NULL DEFAULT '', zotero TEXT NOT NULL DEFAULT '',
    note_error TEXT NOT NULL DEFAULT '', zotero_error TEXT NOT NULL DEFAULT '',
    PRIMARY KEY(project, record)
);
CREATE TABLE IF NOT EXISTS saved_ideas (
    id TEXT PRIMARY KEY, day TEXT NOT NULL, payload TEXT NOT NULL
);
"""

SIGNALS = (
    "benchmark", "benchmarks", "benchmarking", "external validation", "independent cohort", "replication", "reproducibility",
    "dataset", "datasets", "open source", "open-source", "software", "pipeline", "reference panel", "guideline",
    "compared", "evaluated", "validated", "assessed", "multi-center", "multicenter",
    "systematic review", "meta-analysis", "prospective cohort", "randomized", "new method",
    "mechanism", "functional validation", "released", "publicly available",
)
BIOINFO = (
    "bioinformatics", "computational biology", "genome assembly", "variant calling", "pangenome",
    "sequence alignment", "single-cell", "spatial transcriptomics", "proteogenomics", "genomic foundation model",
)
BIOLOGY = (
    "human genetics", "functional genomics", "gene regulation", "cancer biology", "human genome",
    "functional validation", "genetic association", "cancer genomics", "biomarker",
    "cell atlas", "mechanism", "pharmacogenomics", "gene expression",
)


class Busy(Exception):
    pass


@contextlib.contextmanager
def locked(directory):
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "literature.lock").open("a+b") as handle:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise Busy("A literature operation is already running") from exc
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise Busy("A literature operation is already running") from exc
            yield


@contextlib.contextmanager
def database(directory):
    directory.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(directory / "literature.sqlite", timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.executescript(SCHEMA)
        with connection:
            yield connection
    finally:
        connection.close()


def _json(value):
    return json.dumps(value, ensure_ascii=False)


def _record(payload):
    data = json.loads(payload)
    data["authors"] = tuple(data.get("authors", ()))
    return Record(**data)


def stamp(now):
    return now.astimezone(UTC).isoformat(timespec="seconds")


def phrase_hits(text, terms):
    return [term for term in terms if re.search(r"(?<!\w)" + re.escape(term.casefold()) + r"(?!\w)", text)]


def signals(record):
    text = f"{record.title} {record.excerpt}".casefold().replace("–", "-").replace("‑", "-")
    return text, phrase_hits(text, SIGNALS)


def classify(record, profiles):
    if record.kind == "Newsletter":
        return {}
    text, merits = signals(record)
    title = record.title.casefold().replace("–", "-").replace("‑", "-")
    matches = {}
    for project, profile in profiles.items():
        direct = phrase_hits(text, profile.direct)
        title_hit = phrase_hits(title, profile.direct)
        contexts = phrase_hits(text, profile.context)
        if (profile.require_context and not contexts) or (not direct and len(contexts) < 2):
            continue
        matches[project] = {
            "strong": bool(direct and (title_hit or len(direct) > 1 or contexts)
                           and (merits or record.kind == "Guideline")
                           and (record.excerpt or record.kind == "Guideline")),
            "reason": "Topic match: " + ", ".join((direct + contexts)[:4]) + "."
            + (" Source mentions " + ", ".join(merits[:3]) + "." if merits else ""),
            "score": 4 * len(title_hit) + 2 * len(direct) + len(contexts) + min(len(merits), 4),
        }
    return matches


def _caveat(record):
    if record.kind == "Preprint":
        return "Preprint. Findings are provisional."
    if record.kind == "Newsletter":
        return "Newsletter commentary. Follow the original research before drawing conclusions."
    if not record.excerpt:
        return "No public abstract was retrieved."
    return "Based on the indexed abstract. Study details and limitations require the full paper."


def _candidate(record, *, reason=""):
    return {**record.json(), "reason": reason, "caveat": _caveat(record),
            "summary": record.excerpt[:430].rstrip() + ("…" if len(record.excerpt) > 430 else "")}


def _safe_error(exc):
    if isinstance(exc, (SourceError, DestinationError, SynthError)):
        return str(exc)
    if isinstance(exc, PermissionError):
        return "Access denied"
    return f"{type(exc).__name__} during processing"


def _save_record(db, record, day):
    values = {"id": record.identity, "doi": record.doi, "pmid": record.pmid, "arxiv": record.arxiv}
    matches = db.execute(
        """SELECT * FROM records WHERE id=:id OR (:doi!='' AND doi=:doi)
           OR (:pmid!='' AND pmid=:pmid) OR (:arxiv!='' AND arxiv=:arxiv) ORDER BY id=:id DESC""", values
    ).fetchall()
    for row in matches:
        prior = _record(row["payload"])
        if row["id"] != record.identity and ((prior.kind == "Preprint") != (record.kind == "Preprint")):
            continue
        # Keep the existing ID so earlier filing state and report links remain valid.
        updated = bool(record.updated and record.updated > prior.updated)
        replacement = record if updated or (not prior.excerpt and record.excerpt) else prior
        merged = replace(replacement, identity=prior.identity, doi=prior.doi or record.doi,
                         pmid=prior.pmid or record.pmid, arxiv=prior.arxiv or record.arxiv,
                         related_doi=record.related_doi or prior.related_doi,
                         related_pmid=record.related_pmid or prior.related_pmid)
        db.execute("UPDATE records SET doi=?, pmid=?, arxiv=?, payload=? WHERE id=?",
                   (merged.doi, merged.pmid, merged.arxiv, _json(merged.json()), merged.identity))
        if updated:
            db.execute("INSERT OR IGNORE INTO seen VALUES (?,?)", (day, merged.identity))
        return
    db.execute("INSERT INTO records VALUES (?,?,?,?,?)",
               (record.identity, record.doi, record.pmid, record.arxiv, _json(record.json())))
    db.execute("INSERT INTO seen VALUES (?,?)", (day, record.identity))


def _build(records, profiles):
    match_by_id = {r.identity: classify(r, profiles) for r in records}
    project_items = {}
    cross = {"bioinformatics": [], "biology": [], "newsletters": []}
    for project in profiles:
        matches = [(r, match_by_id[r.identity][project]) for r in records if project in match_by_id[r.identity]]
        project_items[project] = sorted(matches, key=lambda row: (
            -int(row[1]["strong"]), -row[1]["score"], row[0].identity
        ))
    for r in records:
        text, merits = signals(r)
        if r.kind == "Newsletter":
            cross["newsletters"].append((r, len(phrase_hits(text, BIOINFO + BIOLOGY)), "Selected newsletter"))
            continue
        if not merits:
            continue
        for key, topics in (("bioinformatics", BIOINFO), ("biology", BIOLOGY)):
            hits = phrase_hits(text, topics)
            if hits:
                cross[key].append((r, 2 * len(hits) + len(merits),
                                   "Topics: " + ", ".join(hits[:3]) + ". Source mentions " + ", ".join(merits[:2]) + "."))
    return project_items, {
        key: [_candidate(r, reason=reason) for r, _, reason in sorted(
            items, key=lambda item: (-item[1], item[0].identity))[:4]]
        for key, items in cross.items()
    }


def _stage_filings(db, day, project_items, projects, cap):
    for key, items in project_items.items():
        if projects[key].read_only:
            continue
        count = db.execute("SELECT count(*) FROM filings WHERE day=? AND project=? AND automatic=1",
                           (day, key)).fetchone()[0]
        for record, match in items:
            if db.execute("SELECT 1 FROM filings WHERE project=? AND record=?", (key, record.identity)).fetchone():
                continue
            auto = match["strong"] and count < cap
            count += int(auto)
            db.execute("""INSERT INTO filings (project,record,day,decision,reason,automatic) VALUES (?,?,?,?,?,?)""",
                       (key, record.identity, day, "accepted" if auto else "review", match["reason"], int(auto)))


def _link_versions(db, records):
    linked = []
    for record in records:
        links = []
        clauses, params = [], []
        for own, relation, column in ((record.doi, record.related_doi, "doi"),
                                       (record.pmid, record.related_pmid, "pmid")):
            if relation:
                clauses.append(f"{column}=?")
                params.append(relation)
            if own:
                clauses.append(f"json_extract(payload, '$.related_{column}')=?")
                params.append(own)
        if clauses:
            rows = db.execute(f"SELECT payload FROM records WHERE {' OR '.join(clauses)}", params)
            for row in rows:
                match = _record(row["payload"])
                if match.identity != record.identity:
                    links.append({"id": match.identity, "title": match.title, "url": match.url, "kind": match.kind})
        if record.related_doi and not links:
            links.append({"url": "https://doi.org/" + record.related_doi, "title": "Related journal DOI", "kind": "Linked work"})
        if record.related_pmid and not links:
            links.append({"url": "https://pubmed.ncbi.nlm.nih.gov/" + record.related_pmid + "/", "title": "Related PubMed record", "kind": "Linked work"})
        linked.append((record.identity, links))
    return dict(linked)


def _flush(directory, projects, project=None):
    zotero = Zotero(directory)
    attempted_connect = False
    zotero_error = ""
    with database(directory) as db:
        rows = db.execute("""SELECT f.*, r.payload FROM filings f JOIN records r ON r.id=f.record
                             WHERE decision='accepted' AND (note='' OR zotero='') AND (? IS NULL OR project=?)
                             ORDER BY day, project, record""", (project, project)).fetchall()
    for row in rows:
        target = projects.get(row["project"])
        if not target or target.read_only:
            continue
        record = _record(row["payload"])
        for column in ("note", "zotero"):
            if row[column]:
                continue
            value = error = ""
            try:
                if column == "note":
                    value = save_note(target, record, row["reason"])
                else:
                    if not attempted_connect:
                        attempted_connect = True
                        try:
                            zotero.connect()
                        except (DestinationError, OSError, KeyError, ValueError) as exc:
                            zotero_error = _safe_error(exc)
                    if zotero_error:
                        raise DestinationError(zotero_error)
                    value = zotero.save(target, record)
            except (DestinationError, OSError, KeyError, ValueError) as exc:
                error = _safe_error(exc)
            with database(directory) as db:
                db.execute(f"""UPDATE filings SET {column}=?, {column}_error=?
                               WHERE project=? AND record=?""", (value, error, target.key, record.identity))


def _new_report(day):
    return {"day": day, "projects": {}, "bioinformatics": [], "biology": [], "newsletters": [], "ideas": [],
            "sources": [], "total": 0, "ai": {"status": "pending", "detail": "", "input": ""}}


def report_status(db, day):
    row = db.execute("SELECT * FROM reports WHERE day=?", (day,)).fetchone()
    if not row:
        return None
    return {**json.loads(row["payload"]), "state": row["state"], "updated": row["updated"]}


def _save_report(db, report, state, now):
    stored = {k: v for k, v in report.items() if k not in ("state", "updated")}
    db.execute("INSERT OR REPLACE INTO reports VALUES (?,?,?,?)", (report["day"], state, stamp(now), _json(stored)))


def _report_refs(report):
    groups = [*report["projects"].values(), report["bioinformatics"], report["biology"], report["newsletters"]]
    return {item["identity"]: item for group in groups for item in group}


def _evidence_payload(report, settings):
    # Only public fields and project briefs enter the Codex subprocess.
    refs = _report_refs(report)
    listed = sorted(refs)[:28]
    return {
        "project_briefs": {key: profile.brief for key, profile in settings.projects.items()},
        "records": [
            {"id": key, **{field: refs[key][field] for field in ("title", "kind", "url", "source", "published")},
             "excerpt": refs[key]["excerpt"][:1250]}
            for key in listed
        ],
    }


def _enrich(report, previous, settings, root, codex=None):
    payload = _evidence_payload(report, settings)
    fingerprint = hashlib.sha256(_json(payload).encode()).hexdigest()
    if not payload["records"]:
        report["ai"] = {"status": "empty", "detail": "No selected items to synthesize", "input": fingerprint}
        return
    if previous and previous["ai"]["status"] == "complete" and previous["ai"]["input"] == fingerprint:
        report["ai"], report["ideas"] = previous["ai"], previous["ideas"]
        return
    try:
        result = (codex or synthesize)(root, settings.codex, payload)
    except SynthError as exc:
        report["ai"] = {"status": "unavailable", "detail": str(exc), "input": fingerprint}
        return
    report["ai"] = {"status": "complete", "detail": "", "input": fingerprint, "summaries": result["summaries"]}
    report["ideas"] = [{**idea, "id": hashlib.sha256(f"{report['day']}/{i}/{idea['title']}".encode()).hexdigest()[:16]}
                       for i, idea in enumerate(result["ideas"])]


def _sources(directory, settings, now, *, preview, http=None, names=()):
    with contextlib.nullcontext(None) if preview else database(directory) as db:
        checkpoints = {} if preview else {row["name"]: row["checkpoint"] for row in db.execute("SELECT name, checkpoint FROM sources")}
    statuses, collected = [], []
    for name, get in source_jobs(settings, http or PublicHTTP(), checkpoints, now):
        if names and name not in names:
            continue
        state, detail, batch = "complete", "", None
        try:
            batch = get()
            if not batch.finish:
                state, detail = "partial", "Results reached the search limit; narrow this query"
        except (SourceError, OSError, ValueError, KeyError, SyntaxError) as exc:
            state, detail = "failed", _safe_error(exc)
        records = batch.records if batch else []
        collected.extend(records)
        if not preview:
            with database(directory) as db:
                for record in records:
                    _save_record(db, record, now.date().isoformat())
                checkpoint = stamp(now) if state == "complete" else checkpoints.get(name)
                db.execute("INSERT OR REPLACE INTO sources VALUES (?,?,?,?,?)",
                           (name, stamp(now), checkpoint, state, detail))
        statuses.append({"name": name, "status": state, "detail": detail, "count": len(records)})
    return statuses, collected


def _finish_run(directory, cfg, now, previous, *, http, codex, draft, force):
    settings = cfg.literature
    day = now.date().isoformat()
    names = tuple(s["name"] for s in previous["sources"] if s["status"] != "complete") if previous and not force else ()
    if previous and previous["sources"] and not names and not force:
        statuses = previous["sources"]
    else:
        updated, _ = _sources(directory, settings, now, preview=False, http=http, names=names)
        statuses = list({s["name"]: s for s in [*(previous or {}).get("sources", []), *updated]}.values())
    with database(directory) as db:
        cached = db.execute("SELECT r.payload FROM records r JOIN seen s ON r.id=s.record WHERE s.day=?", (day,))
        records = [_record(row["payload"]) for row in cached]
        matches, cross = _build(records, settings.projects)
        _stage_filings(db, day, matches, cfg.projects, settings.auto_limit if settings.filing_enabled else 0)
        report = {**_new_report(day), **cross, "sources": statuses, "total": len(records)}
        report["projects"] = {key: [_candidate(r, reason=m["reason"]) for r, m in items[:6]] for key, items in matches.items()}
        links = _link_versions(db, records)
        for group in [*report["projects"].values(), report["bioinformatics"], report["biology"], report["newsletters"]]:
            for item in group:
                item["related"] = links.get(item["identity"], [])
        _save_report(db, report, "running", now)
    if not draft:
        _flush(directory, cfg.projects)
    _enrich(report, previous, settings, config.ROOT, codex)
    state = ("failed" if statuses and all(source["status"] == "failed" for source in statuses) and not records
             else "partial" if any(source["status"] != "complete" for source in statuses)
             or report["ai"]["status"] == "unavailable" else "complete")
    with database(directory) as db:
        _save_report(db, report, state, datetime.now(settings.timezone))
    return {**report, "state": state}


def run(cfg, directory=None, *, now=None, preview=False, draft=False, force=False, http=None, codex=None):
    directory = directory or config.DATA
    settings = cfg.literature
    draft = draft or not settings.filing_enabled
    now = (now or datetime.now(settings.timezone)).astimezone(settings.timezone)
    day = now.date().isoformat()
    if preview:
        statuses, records = _sources(directory, settings, now, preview=True, http=http)
        matches, cross = _build(list({r.identity: r for r in records}.values()), settings.projects)
        return {"day": day, "state": "preview", "sources": statuses, "total": len(records),
                "projects": {key: [_candidate(r, reason=m["reason"]) for r, m in items[:6]] for key, items in matches.items()},
                **cross}
    with locked(directory):
        with database(directory) as db:
            previous = report_status(db, day)
            if force or not previous or previous["state"] != "complete":
                _save_report(db, previous or _new_report(day), "running", now)
        if previous and previous["state"] == "complete" and not force:
            if not draft:
                _flush(directory, cfg.projects)
            return previous
        try:
            return _finish_run(directory, cfg, now, previous, http=http, codex=codex, draft=draft, force=force)
        except Exception:
            with database(directory) as db:
                _save_report(db, report_status(db, day) or _new_report(day), "failed", now)
            raise


def retry(cfg, project=None, directory=None):
    if not cfg.literature.filing_enabled:
        raise DestinationError("Enable filing in hub.toml before retrying")
    directory = directory or config.DATA
    with locked(directory):
        _flush(directory, cfg.projects, project)


def decide(cfg, project, record, action, directory=None, *, flush=True):
    if action == "save" and not cfg.literature.filing_enabled:
        raise ValueError("Enable filing in hub.toml before saving")
    directory = directory or config.DATA
    target = cfg.projects.get(project)
    if not target or target.read_only:
        raise ValueError("Project cannot file literature")
    with locked(directory):
        with database(directory) as db:
            row = db.execute("SELECT decision FROM filings WHERE project=? AND record=?", (project, record)).fetchone()
            if not row:
                raise LookupError("Unknown paper in this project's queue")
            if row["decision"] == "review":
                db.execute("UPDATE filings SET decision=? WHERE project=? AND record=?",
                           ("accepted" if action == "save" else "dismissed", project, record))
        if action == "save" and flush:
            _flush(directory, cfg.projects, project)
    return {"ok": True}


def view(directory, cfg, *, day=None, project=None):
    today = datetime.now(cfg.literature.timezone).date().isoformat()
    empty = dict(today=today, dates=[], report=None, active=None, saved_ideas=[], queue=[], sources=[],
                 counts=dict(review=0, pending=0, filed=0), latest_complete=None,
                 collection_configured=cfg.literature.configured, filing_enabled=cfg.literature.filing_enabled)
    if not (directory / "literature.sqlite").exists():
        return empty
    with database(directory) as db:
        days = [row["day"] for row in db.execute("SELECT day FROM reports ORDER BY day DESC")]
        latest = db.execute("SELECT MAX(day) AS day FROM reports WHERE state='complete'").fetchone()["day"]
        sources = [dict(r) for r in db.execute("SELECT * FROM sources ORDER BY name")]
        active = db.execute("SELECT day, state, updated FROM reports WHERE day=?", (today,)).fetchone()
        active = dict(active) if active else None
        report = report_status(db, day or (days[0] if days else today))
        if active and active["state"] == "running":
            age = datetime.now(UTC) - datetime.fromisoformat(active["updated"])
            if age > timedelta(minutes=25):
                active["state"] = "interrupted"
                if report and report["day"] == today:
                    report["state"] = "interrupted"
        result = {**empty, "dates": days, "report": report, "latest_complete": latest,
                  "active": active, "sources": sources}
        rows = """FROM filings f JOIN records r ON f.record=r.id WHERE (? IS NULL OR f.project=?)"""
        params = (project, project)
        filings = db.execute("SELECT f.*, r.payload " + rows + """ AND f.decision!='dismissed'
                             ORDER BY (f.decision='accepted' AND f.note!='' AND f.zotero!=''),
                                      f.day DESC, f.project, f.record LIMIT 120""", params)
        result["queue"] = [
            {"paper": _candidate(_record(r["payload"]), reason=r["reason"]),
             **{k: r[k] for k in ("project", "decision", "note", "zotero", "note_error", "zotero_error", "day")}}
            for r in filings
        ]
        counts = db.execute("""SELECT sum(f.decision='review') AS review,
                               sum(f.decision='accepted' AND (f.note='' OR f.zotero='')) AS pending,
                               sum(f.decision='accepted' AND f.note!='' AND f.zotero!='') AS filed """ + rows, params).fetchone()
        result["counts"] = {k: counts[k] or 0 for k in counts.keys()}
        result["saved_ideas"] = [
            {**json.loads(row["payload"]), "day": row["day"]}
            for row in db.execute("SELECT * FROM saved_ideas ORDER BY day DESC, id LIMIT 60")
        ]
        return result


def save_idea(directory, day, idea_id):
    with database(directory) as db:
        report = report_status(db, day)
        idea = next((idea for idea in (report or {}).get("ideas", []) if idea["id"] == idea_id), None)
        if not idea:
            raise LookupError("Unknown morning idea")
        refs = _report_refs(report)
        evidence = {key: {"url": refs[key]["url"], "title": refs[key]["title"]}
                    for key in idea["record_ids"] if key in refs}
        db.execute("INSERT OR IGNORE INTO saved_ideas VALUES (?,?,?)", (idea_id, day, _json({**idea, "evidence": evidence})))


def export_ris(directory, project):
    if not (directory / "literature.sqlite").exists():
        return ""
    with database(directory) as db:
        rows = db.execute("""SELECT r.payload FROM filings f JOIN records r ON f.record=r.id
                             WHERE f.project=? AND f.decision='accepted' AND f.zotero='' ORDER BY f.day, f.record""", (project,))
        return ris([_record(row["payload"]) for row in rows])


def print_report(report):
    print(f"{report['day']} | {report['state']} | {report['total']} records", flush=True)
    for source in report["sources"]:
        print(f"  {source['name']}: {source['status']} ({source['count']}) {source['detail']}", flush=True)
    for project, items in report["projects"].items():
        print(f"  {project}: {len(items)} selections", flush=True)


def cli(argv):
    import argparse
    import sys

    parser = argparse.ArgumentParser(prog="python -m hub literature")
    sub = parser.add_subparsers(dest="command", required=True)
    daily = sub.add_parser("run")
    mode = daily.add_mutually_exclusive_group()
    mode.add_argument("--preview", action="store_true", help="Collect without persisting, filing, or calling Codex")
    mode.add_argument("--draft", action="store_true", help="Store report and queue without filing")
    daily.add_argument("--force", action="store_true", help="Check for fresh items again today")
    daily.add_argument("--log", action="store_true", help="Append scheduled output to data/literature.log")
    sub.add_parser("retry")
    zsetup = sub.add_parser("zotero-setup")
    zsetup.add_argument("--collection", action="append", default=[], metavar="PROJECT=KEY")
    zsetup.add_argument("--create", action="store_true", help="Create missing project collections")
    args = parser.parse_args(argv)
    cfg = config.load()
    try:
        if args.command == "run":
            if args.log:
                config.DATA.mkdir(parents=True, exist_ok=True)
                log = config.DATA / "literature.log"
                if log.exists() and log.stat().st_size > 1024 * 1024:
                    log.replace(log.with_suffix(".log.1"))
                with log.open("a", encoding="utf-8") as stream, contextlib.redirect_stdout(stream):
                    try:
                        result = run(cfg, preview=args.preview, draft=args.draft, force=args.force)
                        print_report(result)
                    except Exception as exc:
                        print(f"{datetime.now(cfg.literature.timezone).isoformat()} | failed | {_safe_error(exc)}", flush=True)
                        raise
            else:
                result = run(cfg, preview=args.preview, draft=args.draft, force=args.force)
                print_report(result)
            return int(result["state"] == "failed" or all(s["status"] == "failed" for s in result["sources"]))
        if args.command == "retry":
            retry(cfg)
            return 0
        try:
            mapping = dict(value.split("=", 1) for value in args.collection)
        except ValueError:
            parser.error("Collection mappings must use PROJECT=KEY")
        if mapping.keys() - cfg.literature.projects.keys():
            parser.error("Collection mapping refers to an unknown literature project")
        with locked(config.DATA):
            configured = [cfg.projects[key] for key in cfg.literature.projects if not cfg.projects[key].read_only]
            print("Mapped collections:", Zotero(config.DATA).setup(configured, mapping, create=args.create))
        return 0
    except (DestinationError, Busy) as exc:
        print(str(exc), file=sys.stderr)
        return int(not isinstance(exc, Busy))
