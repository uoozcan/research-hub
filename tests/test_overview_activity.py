from datetime import date
from types import SimpleNamespace

from fastapi import HTTPException
import pytest

from hub import app, config, db, overview_activity


@pytest.fixture
def activity_index(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA", tmp_path)
    monkeypatch.setattr(db, "DB", tmp_path / "hub.sqlite")
    monkeypatch.setattr(app, "cfg", lambda: SimpleNamespace(
        tz=config.ZoneInfo("Europe/Helsinki"),
        projects={"alpha": SimpleNamespace(key="alpha"), "beta": SimpleNamespace(key="beta")},
        allowed_roots=lambda: [tmp_path],
    ))
    record = tmp_path / "results.md"
    record.write_text("Results", encoding="utf-8")
    guarded = tmp_path / "sealed_holdout" / "private.md"
    alternate = tmp_path.parent / "elsewhere.md"
    entries = [
        ("a", "2026-10-24", "alpha", "git", "commit", "Changes", "repo@123456789", 1),
        ("b", "2026-10-25", "alpha", "git", "release", "Tagged version", "repo@v1", 1),
        ("c", "2026-10-25", "alpha", "file", "files", "Batch outputs", "outputs", 509),
        ("d", "2026-10-25", "alpha", "claude", "session", "Private title", "session", 1000),
        ("e", "2026-10-25", "alpha", "report", "report", "Verified results", str(record), 1),
        ("f", "2026-10-25", "beta", "codex", "session", "Private prompt", "session", 4),
        ("g", "2026-10-25", "beta", "decision", "decision", "Choose baseline", str(alternate), 1),
        ("h", "2026-10-25", "beta", "decision", "decision", "Sealed", str(guarded), 1),
        ("i", "2026-10-26", "beta", "vault", "handoff", "Next steps", str(record), 1),
        ("j", "2026-10-25", "retired", "git", "commit", "Old project", "", 1),
        ("k", "2026-10-28", "alpha", "git", "commit", "Future work", "", 1),
    ]
    with db.connect() as connection:
        connection.executemany(
            """INSERT INTO events (id, day, project, source, kind, title, ref, weight, ts, detail)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, '2026-10-25T06:00:00Z', 'secret detail')""", entries,
        )
        connection.executemany(
            """INSERT INTO pomodoros (project, day, kind, minutes, started, ended, note)
               VALUES (?, ?, ?, ?, '2026-10-25T07:00:00Z', '2026-10-25T07:25:00Z', 'private note')""",
            [("alpha", "2026-10-25", "focus", 25), ("alpha", "2026-10-25", "break", 5)],
        )
        connection.execute("INSERT INTO meta VALUES ('refreshed','2026-10-25T09:00:00+02:00')")
    return record


def test_indexed_counts_are_consistent_with_day_detail(activity_index):
    end = date(2026, 10, 26)
    window = overview_activity.window(["alpha", "beta"], 365, end, "Europe/Helsinki")
    assert window["start"] == "2025-10-27"
    assert window["end"] == "2026-10-26"
    assert window["refreshed"] == "2026-10-25T09:00:00+02:00"
    assert window["timezone"] == "Europe/Helsinki"
    assert [(r["day"], r["total"]) for r in window["days"]] == [
        ("2026-10-24", 1), ("2026-10-25", 7), ("2026-10-26", 1),
    ]
    spotlight = window["days"][1]
    day = date(2026, 10, 25)
    all_detail = app.overview_activity_day(day)
    assert all_detail["total"] == spotlight["total"] == len(all_detail["entries"])
    assert all_detail["counts"] == spotlight["counts"]
    detail = app.overview_activity_day(day, "alpha")
    assert detail["total"] == 5
    assert detail["counts"] == {"ai": 1, "files": 1, "focus": 1, "report": 1, "release": 1}
    assert {item["category"]: item for item in detail["entries"]}["files"]["files"] == 509
    assert {item["category"]: item for item in detail["entries"]}["report"]["path"] == str(activity_index)
    decision, = [item for item in all_detail["entries"] if item["category"] == "decision"]
    assert "path" not in decision
    assert {item["title"] for item in all_detail["entries"] if item["category"] == "ai"} == {
        "Codex session", "Claude Code session",
    }
    assert all("detail" not in item for item in all_detail["entries"])


def test_civil_windows_include_full_days_across_timezone_changes(activity_index):
    end = date(2026, 10, 26)
    year = overview_activity.window(["alpha"], 365, end, "Europe/Helsinki")
    recent = overview_activity.window(["alpha"], 90, end, "Europe/Helsinki")
    assert recent["start"] == "2026-07-29"
    assert (date.fromisoformat(year["end"]) - date.fromisoformat(year["start"])).days + 1 == 365
    assert (date.fromisoformat(recent["end"]) - date.fromisoformat(recent["start"])).days + 1 == 90
    assert recent["days"] == year["days"]


def test_empty_day_and_project_validation(activity_index):
    assert app.overview_activity_day(date(2026, 10, 23), "alpha") == {
        "day": "2026-10-23", "total": 0, "entries": [], "counts": {},
    }
    with pytest.raises(HTTPException) as unknown:
        app.overview_activity_day(date(2026, 10, 25), "retired")
    assert unknown.value.status_code == 404
    with pytest.raises(HTTPException) as invalid:
        app.overview_activity_window(days=7)
    assert invalid.value.status_code == 422
