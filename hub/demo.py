"""Synthetic offline content for evaluating Research Hub on first launch."""

from datetime import datetime, timedelta

from . import config, db, literature


def seed(*, refresh=False):
    current = datetime.now(config.load().tz)
    now = current.isoformat(timespec="seconds")
    day = current.date().isoformat()
    with db.connect() as connection:
        if refresh or not connection.execute("SELECT 1 FROM meta WHERE key='refreshed'").fetchone():
            connection.execute("DELETE FROM events WHERE id LIKE 'demo:%'")
            for offset in (0, 1, 3, 6, 11, 17, 28, 40, 59, 83, 118, 153, 211, 276, 310):
                moment = current - timedelta(days=offset)
                for i in range(1 + offset % 6):
                    project, source, kind, title = (
                        ("methods", "decision", "decision", "Record a reproducible comparison"),
                        ("benchmark", "git", "commit", "Add a synthetic benchmark fixture"),
                        ("methods", "report", "report", "Summarize the latest validation"),
                    )[i % 3]
                    connection.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
                        f"demo:{offset}:{i}", moment.isoformat(), moment.date().isoformat(),
                        project, source, kind, title, "Synthetic demo", "", None, 1,
                    ))
            connection.execute("INSERT OR REPLACE INTO meta VALUES ('refreshed', ?)", (now,))
    with literature.database(config.DATA) as connection:
        if literature.report_status(connection, day):
            return
        report = literature._new_report(day)
        report["projects"] = {
            "methods": [{
                "identity": "demo-example", "source": "Demo", "kind": "Synthetic example",
                "title": "Comparing reproducible workflow runs",
                "url": "", "published": day,
                "summary": "This example displays a generated research record without fetching any external data.",
                "reason": "Demonstrates how project literature appears.", "caveat": "Fictional example.",
            }],
            "benchmark": [],
        }
        report["total"] = 1
        report["ai"] = {"status": "empty", "detail": "", "input": ""}
        report["ideas"] = [{
            "id": "demo-idea", "title": "Test the effect of cache reuse", "kind": "current_project",
            "project": "benchmark", "hypothesis": "Cached inputs reduce elapsed time.",
            "record_ids": ["demo-example"], "first_test": "Compare two runs on a synthetic dataset.",
            "success_criterion": "Outputs match and elapsed time decreases.",
            "uncertainty": "Disk throughput may dominate runtime.",
        }]
        literature._save_report(connection, report, "complete", current)
