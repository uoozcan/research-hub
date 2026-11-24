import sqlite3

from fastapi.testclient import TestClient
import pytest

from hub import config, db, workspace
from hub.app import app


def test_local_web_boundary():
    with TestClient(app, base_url="http://example.test") as remote:
        assert remote.get("/api/health").status_code == 400
    with TestClient(app, base_url="http://127.0.0.1") as local:
        assert local.get("/api/health").json()["application"] == "research-hub"
        assert local.post("/api/refresh", headers={"Origin": "https://example.test"}).status_code == 403
        assert local.post("/api/pomodoro").status_code == 403
        assert "frame-ancestors" in local.get("/").headers["content-security-policy"]


def test_import_keeps_data_local_and_disables_library_writes(tmp_path):
    source = tmp_path / "original"
    (source / "data").mkdir(parents=True)
    (source / "reports").mkdir()
    (source / "reports" / "status.md").write_text("Draft findings", encoding="utf-8")
    (source / "hub.toml").write_text(
        f"[hub]\n[projects.sample]\nname='Sample'\nreports=['{source.as_posix()}/reports']\n"
        "[literature]\nsynthesis_enabled=true\nfiling_enabled=true\n", encoding="utf-8",
    )
    (source / "data" / "zotero-local.json").write_text("{}", encoding="utf-8")
    with sqlite3.connect(source / "data" / "hub.sqlite") as connection:
        connection.execute("CREATE TABLE test (a TEXT)")
        connection.execute("INSERT INTO test VALUES ('original')")
    destination = tmp_path / "copy"
    workspace.import_workspace(source, destination)
    copied = config.load(destination / "hub.toml")
    assert copied.projects["sample"].reports[0].resolve() == destination / "reports"
    assert not copied.literature.codex and not copied.literature.filing_enabled
    assert (destination / "reports" / "status.md").is_file()
    assert not (destination / "data" / "zotero-local.json").exists()
    with sqlite3.connect(destination / "data" / "hub.sqlite") as connection:
        assert connection.execute("SELECT a FROM test").fetchone() == ("original",)
    with pytest.raises(ValueError, match="new workspace"):
        workspace.import_workspace(source, destination)


def test_first_launch_seeds_local_demo(tmp_path, monkeypatch):
    path = tmp_path / "demo"
    monkeypatch.setattr(config, "WORKSPACE", path)
    monkeypatch.setattr(config, "DATA", path / "data")
    monkeypatch.setattr(db, "DATA", path / "data")
    monkeypatch.setattr(db, "DB", path / "data/hub.sqlite")
    load = config.load
    monkeypatch.setattr(config, "load", lambda: load(path / "hub.toml"))
    workspace.prepare()
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert len(client.get("/api/projects").json()["projects"]) == 2
        assert client.get("/api/overview/activity").json()["days"]
        literature = client.get("/api/literature").json()
        assert literature["report"]["total"] == 1
        assert not literature["collection_configured"] and not literature["filing_enabled"]
        assert client.post("/api/literature/run", headers={"Origin": "http://127.0.0.1"}).status_code == 409
        assert client.post("/api/refresh", headers={"Origin": "http://127.0.0.1"}).status_code == 200
    load.cache_clear()
